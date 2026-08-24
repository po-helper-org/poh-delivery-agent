"""Правила релиза: кого берём, в каком порядке, с каким риском.

Модуль чистый и это принципиально. Решение «что отгружать» обязано быть
воспроизводимым и объяснимым: по одному и тому же снимку состояния агент дважды
обязан построить один и тот же план, иначе разбор неудачной выкатки упирается в
«ну модель так решила». Ни одного вызова модели здесь нет и не должно быть —
LLM в этом контуре пишет текст, а очередь считает код.
"""

from poh_delivery.model import (
    CHECKS_PENDING,
    CHECKS_RED,
    CONFLICT,
    DRAFT,
    ELIGIBLE,
    NOT_APPROVED,
    CheckSpec,
    PullFacts,
    ReleasePlan,
    StepPlan,
    Verdict,
)

# Метка-эквивалент одобрения: не в каждом репозитории включены обязательные
# ревью, а «одобрен к merge» решает человек, а не наличие настроенной ветки.
APPROVE_LABELS = ("ready-to-ship", "approved-to-merge")

# Состояния мержабельности GitHub, означающие конфликт с базой.
_CONFLICT_STATES = ("dirty",)
# `behind` — не конфликт: ветка просто отстала, мерж база-в-ветку не нужен,
# GitHub вливает такой PR сам. Отдельным состоянием оно интересно только тем,
# что проверки в PR прогонялись НЕ на текущей базе — это ловит верификация
# после мержа, а не планировщик.
_BEHIND_STATES = ("behind",)


def classify(pr: PullFacts, approve_labels: tuple[str, ...] = APPROVE_LABELS) -> Verdict:
    """Вердикт по одному PR. Порядок проверок = порядок дешевизны отказа."""
    if pr.draft:
        return Verdict(pr.number, DRAFT, "черновик")

    approved = pr.approved or any(label in approve_labels for label in pr.labels)
    if not approved:
        return Verdict(pr.number, NOT_APPROVED,
                       f"нет одобрения (review_decision={pr.review_decision or 'пусто'}, "
                       f"нет метки {'/'.join(approve_labels)})")

    # Конфликт проверяется ДО проверок CI: красный CI на конфликтующей ветке —
    # следствие, и чинить его отдельно бессмысленно.
    if pr.mergeable is False or pr.mergeable_state in _CONFLICT_STATES:
        return Verdict(pr.number, CONFLICT, f"конфликт с базой (state={pr.mergeable_state})")

    if pr.checks_state == "failure":
        return Verdict(pr.number, CHECKS_RED, "проверки красные")
    if pr.checks_state == "pending":
        return Verdict(pr.number, CHECKS_PENDING, "проверки ещё идут")

    if pr.mergeable is None and pr.mergeable_state == "unknown":
        # GitHub считает мержабельность асинхронно. Неизвестность — не разрешение.
        return Verdict(pr.number, CHECKS_PENDING, "GitHub ещё считает мержабельность")

    behind = " (ветка отстала от базы, будет обновлена мержем)" if pr.mergeable_state in _BEHIND_STATES else ""
    return Verdict(pr.number, ELIGIBLE, f"одобрен, конфликтов нет{behind}")


def risk_of(pr: PullFacts) -> str:
    """Грубая оценка риска по объёму правки.

    Специально грубая и не настраиваемая: точность здесь не нужна, нужен
    ПОРЯДОК — что катить первым, а что последним, когда обе правки зелёные.
    """
    touched = len(pr.files)
    volume = pr.additions + pr.deletions
    if touched >= 6 or volume >= 200:
        return "high"
    if touched >= 3 or volume >= 50:
        return "medium"
    return "low"


_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def order_steps(prs: list[PullFacts]) -> list[PullFacts]:
    """Очередь отгрузки: сначала дешёвое и независимое, потом тяжёлое.

    Правило одно и объяснимое: риск по возрастанию, при равном риске — порядок
    появления (номер PR). Дешёвая правка, уехавшая первой, проверяет весь тракт
    выкатки — сборку, деплой, проверки, откат — до того, как через него поедет
    дорогая.
    """
    return sorted(prs, key=lambda p: (_RISK_ORDER[risk_of(p)], p.number))


def _shared_files(a: PullFacts, b: PullFacts) -> list[str]:
    return sorted(set(a.files) & set(b.files))


def build_plan(repo: str, base_sha: str, prs: list[PullFacts], checks: list[CheckSpec],
               tag: str, checks_source: str = "",
               skipped: list[Verdict] | None = None,
               delegated: list[Verdict] | None = None) -> ReleasePlan:
    """Полный план релиза из списка ПРОШЕДШИХ отбор PR."""
    ordered = order_steps(prs)
    check_names = [c.name for c in checks]
    steps: list[StepPlan] = []
    for index, pr in enumerate(ordered, start=1):
        risk = risk_of(pr)
        depends: list[int] = []
        notes: list[str] = []
        for earlier in ordered[: index - 1]:
            shared = _shared_files(earlier, pr)
            if shared:
                depends.append(earlier.number)
                notes.append(f"общие файлы с #{earlier.number}: {', '.join(shared[:3])}")
        reason = f"риск {risk}, {len(pr.files)} файл(ов), +{pr.additions}/-{pr.deletions}"
        if notes:
            # Общий файл — причина идти строго после соседа, а не рядом с ним:
            # иначе второй мерж принесёт конфликт, которого в плане не было.
            reason += "; " + "; ".join(notes)
        steps.append(StepPlan(
            order=index, pr_number=pr.number, title=pr.title, risk=risk,
            reason=reason, depends_on=depends, files=list(pr.files),
            checks=check_names,
        ))
    return ReleasePlan(
        repo=repo, tag=tag, title=f"Релиз {tag}", base_sha=base_sha, steps=steps,
        skipped=list(skipped or []), delegated=list(delegated or []),
        checks_source=checks_source, checks=list(checks),
    )


def next_tag(existing_tags: list[str], day: str) -> str:
    """Тег релиза вида `release-2026-08-21.1`.

    Дата приходит аргументом, а не берётся из часов: воркфлоу Temporal обязан
    быть детерминированным, и `date.today()` внутри него сломал бы реплей.
    """
    prefix = f"release-{day}."
    used = []
    for tag in existing_tags:
        if tag.startswith(prefix):
            suffix = tag[len(prefix):]
            if suffix.isdigit():
                used.append(int(suffix))
    return f"{prefix}{max(used) + 1 if used else 1}"
