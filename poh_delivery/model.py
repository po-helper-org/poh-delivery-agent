"""Факты и решения релиза — плоские dataclass'ы, ездят через Temporal.

Почему dataclass, а не dict: вход воркфлоу и результаты активностей уезжают в
историю Temporal, и там они живут дольше кода. Словарь в истории не расскажет,
какое поле пропало при переименовании, — типизированный payload расскажет сразу,
на первой же десериализации.

Модуль чистый: ни сети, ни Temporal, ни GitHub.
"""

from dataclasses import dataclass, field

# --- Вердикты по PR: почему он взят в релиз или не взят ---

ELIGIBLE = "eligible"            # в релиз
CONFLICT = "conflict"            # конфликт с базой — сначала к разработчику
NOT_APPROVED = "not-approved"    # нет одобрения человека
CHECKS_RED = "checks-red"        # проверки красные
CHECKS_PENDING = "checks-pending"  # проверки ещё идут
DRAFT = "draft"                  # черновик
REVIEW_BLOCKED = "review-blocked"  # ревью против: замечания в силе либо ждут человека
REVIEW_PENDING = "review-pending"  # вердикта ревью на текущий коммит ещё нет


@dataclass
class PullFacts:
    """Состояние PR на момент сборки релиза.

    Снимается один раз перед планированием и ещё раз перед мержем каждого шага:
    между планом и отгрузкой состояние успевает измениться (влитый сосед делает
    ветку `behind`), и отгружать по устаревшему снимку — тот самый молчаливый
    отказ, ради которого агент и заводится.
    """

    number: int
    title: str = ""
    author: str = ""
    base: str = "main"
    head_ref: str = ""
    head_sha: str = ""
    draft: bool = False
    approved: bool = False
    review_decision: str = ""
    labels: list[str] = field(default_factory=list)
    # None = GitHub ещё считает мержабельность (ответ приходит асинхронно).
    mergeable: bool | None = None
    mergeable_state: str = "unknown"
    checks_state: str = "none"  # success | failure | pending | none
    files: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    updated_at: str = ""
    body: str = ""
    # Когда появился текущий коммит ветки — по нему решается, относится ли
    # вердикт ревью к тому, что вливаем, или к позавчерашнему коду.
    head_committed_at: str = ""
    review_verdict: str = "none"
    review_reason: str = ""


@dataclass
class Verdict:
    """Решение по одному PR с человекочитаемой причиной."""

    number: int
    verdict: str
    reason: str


@dataclass
class CheckSpec:
    """Одна проверка живой системы после выкатки.

    Берётся из `.delivery/checks.json` целевого репозитория: что считать
    «работает как описано в БФТ», решает сам репозиторий, а не агент.
    """

    name: str
    path: str = "/"
    method: str = "GET"
    body: dict | None = None
    expect_status: int = 200
    expect_json: dict = field(default_factory=dict)   # подмножество полей ответа
    expect_contains: str = ""
    # Ссылка на источник требования — БФТ, Issue, пункт HowToDemo.
    source: str = ""


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class StepPlan:
    """Шаг релиза — один PR: что вливаем, чем проверяем, как откатываем."""

    order: int
    pr_number: int
    title: str = ""
    risk: str = "low"          # low | medium | high
    reason: str = ""           # почему именно это место в очереди
    depends_on: list[int] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)  # имена проверок из checks.json
    rollback: str = "revert-and-redeploy"


@dataclass
class ReleasePlan:
    repo: str
    tag: str = ""
    title: str = ""
    base_sha: str = ""
    steps: list[StepPlan] = field(default_factory=list)
    skipped: list[Verdict] = field(default_factory=list)
    delegated: list[Verdict] = field(default_factory=list)
    checks_source: str = ""
    checks: list[CheckSpec] = field(default_factory=list)


@dataclass
class ObservationResult:
    """Результат наблюдения за контейнером после выкатки."""
    
    duration: int
    alive: bool
    restarts: int
    detail: str = ""


@dataclass
class StepOutcome:
    pr_number: int
    ok: bool = False
    merged_sha: str = ""
    deployed: bool = False
    checks: list[CheckResult] = field(default_factory=list)
    rolled_back: bool = False
    detail: str = ""
    observation: ObservationResult | None = None


@dataclass
class DeliveryRequest:
    """Вход воркфлоу: чей репозиторий отгружаем и кто попросил."""

    repo: str
    requested_by: str = ""
    issue_number: int = 0      # Issue/PR, из которого пришла команда
    comment_id: int = 0
    dry_run: bool = False


@dataclass
class DeployResult:
    ok: bool
    sha: str = ""
    detail: str = ""
    url: str = ""


@dataclass
class RepoState:
    """Снимок репозитория на момент планирования."""

    repo: str
    default_branch: str = "main"
    base_sha: str = ""
    pulls: list[PullFacts] = field(default_factory=list)


@dataclass
class ChecksBundle:
    """Конфигурация проверок, прочитанная из целевого репозитория."""

    source: str = ""
    service: dict = field(default_factory=dict)
    checks: list[CheckSpec] = field(default_factory=list)


@dataclass
class ReleaseRef:
    release_id: int = 0
    url: str = ""
    tag: str = ""


@dataclass
class ReviewRound:
    """Итог одного круга ревью-правок, выполненного агентом разработки."""

    settled: bool = False      # правок не потребовалось — вердикт «замечаний нет»
    changed: bool = False      # правки внесены, нужно новое ревью
    detail: str = ""
