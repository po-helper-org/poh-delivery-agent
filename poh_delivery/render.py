"""Тексты релиза: план до выкатки и отчёт после.

Тексты собираются кодом, а не моделью. Релиз — документ, по которому потом
разбирают инцидент: в нём важны точность номеров, порядка и SHA, а не гладкость
формулировок. Модель здесь добавила бы ровно один риск — красиво соврать.

Модуль чистый: ни сети, ни Temporal, ни GitHub.
"""

import re

from poh_delivery.model import CheckSpec, ReleasePlan, StepOutcome, Verdict

_CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)

_VERDICT_RU = {
    "conflict": "конфликт с базой",
    "not-approved": "нет одобрения",
    "checks-red": "проверки красные",
    "checks-pending": "проверки не завершены",
    "draft": "черновик",
    "review-blocked": "ревью против мержа",
    "review-pending": "нет вердикта ревью",
}


def linked_issue(body: str) -> int | None:
    """Номер Issue, который закрывает PR, если он назван в теле."""
    match = _CLOSES_RE.search(body or "")
    return int(match.group(1)) if match else None


def _checks_block(checks: list[CheckSpec], source: str) -> str:
    if not checks:
        return ("**Проверки:** не заданы — в репозитории нет `.delivery/checks.json`. "
                "Выкатка проверяется только тем, что сервис поднялся.\n")
    lines = [f"**Проверки после каждого шага** (источник — `{source}`):", ""]
    for check in checks:
        expect = f"HTTP {check.expect_status}"
        if check.expect_json:
            expect += ", " + ", ".join(f"`{k}={v}`" for k, v in check.expect_json.items())
        if check.expect_contains:
            expect += f", тело содержит `{check.expect_contains}`"
        origin = f" — {check.source}" if check.source else ""
        lines.append(f"- `{check.name}`: {check.method} `{check.path}` → {expect}{origin}")
    lines.append("")
    return "\n".join(lines)


def plan_md(plan: ReleasePlan, requested_by: str, run_id: str, workflow_id: str,
            org_rules: str = "") -> str:
    """Тело релиза до выкатки: что, в каком порядке, чем проверяем, как откатываем.

    `org_rules` — блок правил и накопленного опыта организации от слоя
    саморефлексии. Пусто (слой не подключён) — документ собирается ровно как
    раньше, ни на символ не отличаясь.

    Блок кладётся в план, который читает ЧЕЛОВЕК перед одобрением выкатки: это
    и есть способ, которым накопленное знание о ландшафте влияет на решение.
    Модели в этом контуре нет и не будет — очередь считает код.
    """
    out: list[str] = []
    out.append("## План релиза")
    out.append("")
    out.append(f"Собран Delivery-Agent по запросу @{requested_by}." if requested_by
               else "Собран Delivery-Agent.")
    out.append("")
    out.append(f"- Репозиторий: `{plan.repo}`")
    out.append(f"- База: `{plan.base_sha[:12]}`" if plan.base_sha else "- База: неизвестна")
    out.append(f"- Шагов: {len(plan.steps)}")
    out.append(f"- Temporal: `{workflow_id}` / `{run_id}`")
    out.append("")

    if plan.steps:
        out.append("### Очередь отгрузки")
        out.append("")
        out.append("| # | PR | Риск | Почему здесь | Зависит от |")
        out.append("|---|----|------|--------------|------------|")
        for step in plan.steps:
            depends = ", ".join(f"#{n}" for n in step.depends_on) or "—"
            out.append(f"| {step.order} | #{step.pr_number} {step.title} | {step.risk} "
                       f"| {step.reason} | {depends} |")
        out.append("")
    else:
        out.append("### Очередь отгрузки")
        out.append("")
        out.append("Пусто — ни один PR не прошёл отбор.")
        out.append("")

    out.append(_checks_block(plan.checks, plan.checks_source))

    out.append("### Порядок выкатки одного шага")
    out.append("")
    out.append("1. Пересъём состояния PR — актуален ли он ещё, нет ли конфликта, "
               "относится ли вердикт ревью к текущему коммиту.")
    out.append("2. Мерж в базовую ветку.")
    out.append("3. Выкатка получившегося состояния базы на прод-контур.")
    out.append("4. Прогон проверок из списка выше по живому сервису.")
    out.append("5. Провал любой проверки — откат: возврат прошлой сборки и `git revert` мержа.")
    out.append("   Остаток очереди не отгружается.")
    out.append("")

    if plan.delegated:
        out.append("### Отдано разработчику (конфликты)")
        out.append("")
        for verdict in plan.delegated:
            out.append(f"- #{verdict.number} — {verdict.reason}")
        out.append("")

    if plan.skipped:
        out.append("### Не вошло в релиз")
        out.append("")
        for verdict in plan.skipped:
            label = _VERDICT_RU.get(verdict.verdict, verdict.verdict)
            out.append(f"- #{verdict.number} — {label}: {verdict.reason}")
        out.append("")

    if org_rules.strip():
        out.append("### Накопленный опыт этой организации")
        out.append("")
        out.append(org_rules.strip())
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def pr_announcement(plan: ReleasePlan, release_url: str, step_order: int) -> str:
    """Комментарий в PR: он в релизе, вот ссылка и его место в очереди."""
    return (
        f"**Delivery-Agent: PR включён в релиз `{plan.tag}`.**\n\n"
        f"Место в очереди — шаг {step_order} из {len(plan.steps)}. "
        f"План отгрузки, проверки и порядок отката: {release_url}\n\n"
        f"После мержа состояние базы выкатывается на прод-контур и проверяется "
        f"по списку из плана. Провал проверки означает откат этого шага и остановку релиза."
    )


def _outcome_line(outcome: StepOutcome) -> str:
    if outcome.ok:
        return f"✅ #{outcome.pr_number} — влит `{outcome.merged_sha[:12]}`, проверки зелёные"
    if outcome.rolled_back:
        return f"⛔ #{outcome.pr_number} — откачен: {outcome.detail}"
    return f"⚠️ #{outcome.pr_number} — не отгружен: {outcome.detail}"


def report_md(plan: ReleasePlan, outcomes: list[StepOutcome], bodies: dict[int, str],
              run_id: str, workflow_id: str, finished_at: str) -> str:
    """Тело релиза после выкатки: что сделано, что изменилось для пользователей."""
    shipped = [o for o in outcomes if o.ok]
    failed = [o for o in outcomes if not o.ok]
    not_reached = [s for s in plan.steps
                   if s.pr_number not in {o.pr_number for o in outcomes}]

    out: list[str] = []
    out.append("## Результат выкатки")
    out.append("")
    out.append(f"Отгружено {len(shipped)} из {len(plan.steps)} шагов. "
               f"Завершено {finished_at}.")
    out.append("")
    for outcome in outcomes:
        out.append(_outcome_line(outcome))
    for step in not_reached:
        out.append(f"⏭️ #{step.pr_number} — не дошёл: релиз остановлен раньше")
    out.append("")

    if shipped:
        out.append("### Что изменилось для пользователей")
        out.append("")
        for outcome in shipped:
            step = next((s for s in plan.steps if s.pr_number == outcome.pr_number), None)
            title = step.title if step else ""
            issue = linked_issue(bodies.get(outcome.pr_number, ""))
            origin = f" (задача #{issue})" if issue else ""
            files = ", ".join(f"`{f}`" for f in (step.files[:4] if step else []))
            out.append(f"- **{title}**{origin} — затронуто: {files or '—'}")
        out.append("")

    if any(o.checks for o in outcomes):
        out.append("### Проверки по шагам")
        out.append("")
        out.append("| PR | Проверка | Итог |")
        out.append("|----|----------|------|")
        for outcome in outcomes:
            for check in outcome.checks:
                mark = "зелёная" if check.ok else f"**красная** — {check.detail}"
                out.append(f"| #{outcome.pr_number} | `{check.name}` | {mark} |")
        out.append("")

    if failed:
        out.append("### Что пошло не так")
        out.append("")
        for outcome in failed:
            out.append(f"- #{outcome.pr_number}: {outcome.detail}")
            if outcome.rolled_back:
                out.append("  Изменение отменено, база возвращена в прежнее состояние.")
        out.append("")

    out.append("### Взаимосвязанные изменения")
    out.append("")
    out.append(f"- Репозиторий кода: `{plan.repo}`, база `{plan.base_sha[:12]}` → "
               f"`{(shipped[-1].merged_sha[:12] if shipped else plan.base_sha[:12])}`")
    out.append(f"- Прод-контур: выкатан из базовой ветки после каждого шага")
    out.append(f"- Temporal: `{workflow_id}` / `{run_id}` — полная история шагов и проверок")
    out.append("")
    return "\n".join(out).rstrip() + "\n"


def summary_comment(plan: ReleasePlan, outcomes: list[StepOutcome], release_url: str) -> str:
    """Короткий итог в Issue, из которого запускали релиз."""
    shipped = sum(1 for o in outcomes if o.ok)
    lines = [f"**Delivery-Agent: релиз `{plan.tag}` завершён.**", "",
             f"Отгружено {shipped} из {len(plan.steps)} шагов. Подробности: {release_url}", ""]
    for outcome in outcomes:
        lines.append(_outcome_line(outcome))
    if plan.delegated:
        lines.append("")
        lines.append("Отдано разработчику на разрешение конфликтов: "
                     + ", ".join(f"#{v.number}" for v in plan.delegated))
    return "\n".join(lines)


def verdict_lines(verdicts: list[Verdict]) -> str:
    return "\n".join(f"- #{v.number} — {_VERDICT_RU.get(v.verdict, v.verdict)}: {v.reason}"
                     for v in verdicts)
