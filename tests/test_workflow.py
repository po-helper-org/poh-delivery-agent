"""Воркфлоу релиза на тестовом окружении Temporal, активности — заглушки.

Проверяется то, чего чистые тесты правил не видят: ПОРЯДОК внешних эффектов.
Релиз ошибается не в арифметике, а в последовательности — выкатить до мержа,
проверить до выкатки, продолжить очередь после провала. Здесь каждый эффект
записывается в журнал, и утверждения делаются по журналу.
"""

import uuid

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from poh_delivery.model import (
    CheckResult,
    CheckSpec,
    ChecksBundle,
    DeliveryRequest,
    DeployResult,
    PullFacts,
    ReleaseRef,
    RepoState,
)
from poh_delivery.workflow import DeliveryRelease

JOURNAL: list[str] = []
STATE: dict = {}


def _pr(number: int, **kwargs) -> PullFacts:
    base = dict(number=number, title=f"PR {number}", approved=True, mergeable=True,
                mergeable_state="clean", checks_state="success",
                files=[f"src/f{number}.mjs"], additions=10, deletions=0,
                body=f"Closes #{number * 10}")
    base.update(kwargs)
    return PullFacts(**base)


@activity.defn(name="delivery_collect_state")
async def collect_state(repo: str) -> RepoState:
    JOURNAL.append("collect_state")
    return RepoState(repo=repo, default_branch="main", base_sha="base000",
                     pulls=list(STATE["pulls"]))


@activity.defn(name="delivery_read_checks")
async def read_checks(repo: str, ref: str) -> ChecksBundle:
    return ChecksBundle(source=".delivery/checks.json@main",
                        service={"port": 8080, "start": "node src/server.mjs"},
                        checks=[CheckSpec(name="quote-base")])


@activity.defn(name="delivery_pull_facts")
async def pull_facts(repo: str, number: int) -> PullFacts:
    JOURNAL.append(f"facts:{number}")
    # Очередь ответов на один PR: GitHub отдаёт состояние не сразу, и релиз
    # обязан пережить «ещё считаю» между двумя чтениями.
    queue = STATE.get("facts_seq", {}).get(number)
    if queue:
        return queue.pop(0) if len(queue) > 1 else queue[0]
    return STATE["facts"][number]


@activity.defn(name="delivery_existing_tags")
async def existing_tags(repo: str) -> list[str]:
    return []


@activity.defn(name="delivery_create_release")
async def create_release(repo: str, tag: str, title: str, body: str, target: str) -> ReleaseRef:
    JOURNAL.append(f"release:{tag}")
    STATE["release_body"] = body
    return ReleaseRef(release_id=1, url="https://example/release/1", tag=tag)


@activity.defn(name="delivery_update_release")
async def update_release(repo: str, release_id: int, body: str, publish: bool) -> str:
    JOURNAL.append(f"publish:{publish}")
    STATE["final_body"] = body
    return "https://example/release/1"


@activity.defn(name="delivery_comment")
async def comment(repo: str, number: int, body: str) -> None:
    JOURNAL.append(f"comment:{number}")
    STATE.setdefault("comments", []).append((number, body))


@activity.defn(name="delivery_merge")
async def merge(repo: str, number: int, method: str) -> str:
    JOURNAL.append(f"merge:{number}")
    return f"merged{number}"


@activity.defn(name="delivery_deploy")
async def deploy(repo: str, sha: str, service: dict) -> DeployResult:
    JOURNAL.append(f"deploy:{sha}")
    if sha in STATE.get("deploy_fails", ()):
        return DeployResult(ok=False, sha=sha, detail="сервис не поднялся")
    return DeployResult(ok=True, sha=sha, detail="ok")


@activity.defn(name="delivery_verify")
async def verify(checks: list[CheckSpec], service: dict) -> list[CheckResult]:
    JOURNAL.append("verify")
    if STATE.get("verify_fail_after", 99) <= JOURNAL.count("verify"):
        return [CheckResult("quote-base", False, "ожидался HTTP 200, пришёл 500")]
    return [CheckResult("quote-base", True, "HTTP 200")]


@activity.defn(name="delivery_revert")
async def revert(repo: str, merge_sha: str, branch: str) -> str:
    JOURNAL.append(f"revert:{merge_sha}")
    return "revert111"


@activity.defn(name="delivery_fix_conflicts")
async def fix_conflicts(repo: str, number: int) -> str:
    JOURNAL.append(f"fix:{number}")
    STATE["facts"][number] = _pr(number)  # круг правок развёл конфликт
    return "конфликт разрешён"


ACTIVITIES = [collect_state, read_checks, pull_facts, existing_tags, create_release,
              update_release, comment, merge, deploy, verify, revert, fix_conflicts]


async def _run(pulls, **state) -> dict:
    JOURNAL.clear()
    STATE.clear()
    STATE["pulls"] = pulls
    STATE["facts"] = {p.number: p for p in pulls}
    STATE.update(state)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        client: Client = env.client
        # Активности Harness (`delivery_fix_conflicts`) живут на СВОЕЙ очереди —
        # в тесте поднимаем обе, иначе делегирование конфликта не проверить.
        async with Worker(client, task_queue="delivery", workflows=[DeliveryRelease],
                          activities=ACTIVITIES,
                          workflow_runner=UnsandboxedWorkflowRunner()), \
                   Worker(client, task_queue="issue-lifecycle", activities=[fix_conflicts]):
            return await client.execute_workflow(
                DeliveryRelease.run,
                DeliveryRequest(repo="o/r", requested_by="kibarik", issue_number=99),
                id=f"delivery-test-{uuid.uuid4()}", task_queue="delivery")


@pytest.mark.asyncio
async def test_happy_path_ships_in_planned_order():
    heavy = _pr(2, files=[f"x{i}" for i in range(8)], additions=400)
    light = _pr(5)
    result = await _run([heavy, light])

    assert result["shipped"] == [5, 2]
    merges = [entry for entry in JOURNAL if entry.startswith("merge:")]
    assert merges == ["merge:5", "merge:2"]
    # Ровно тот порядок, что записан в плане: мерж → выкатка → проверка.
    window = JOURNAL[JOURNAL.index("merge:5"):]
    assert window[:3] == ["merge:5", "deploy:merged5", "verify"]
    assert "publish:True" in JOURNAL


@pytest.mark.asyncio
async def test_release_link_lands_on_every_pr_before_merge():
    """Ссылка на релиз обязана попасть в PR ДО того, как он влит."""
    await _run([_pr(1), _pr(2)])
    first_merge = next(i for i, entry in enumerate(JOURNAL) if entry.startswith("merge:"))
    release_at = next(i for i, entry in enumerate(JOURNAL) if entry.startswith("release:"))
    announced = [i for i, entry in enumerate(JOURNAL)
                 if entry in ("comment:1", "comment:2") and i < first_merge]
    # Оба PR узнают о релизе до того, как влит первый из них, и уже после того,
    # как черновик релиза создан — иначе в комментарии нечего было бы дать.
    assert len(announced) == 2
    assert release_at < min(announced)


@pytest.mark.asyncio
async def test_failed_check_rolls_back_and_stops_queue():
    result = await _run([_pr(1), _pr(2)], verify_fail_after=1)

    assert result["shipped"] == []
    assert result["failed"] == [1]
    assert "revert:merged1" in JOURNAL
    # Второй PR не трогается вовсе — очередь остановлена.
    assert "merge:2" not in JOURNAL
    # После отката прод возвращается на состояние ревертнутой базы.
    assert JOURNAL.index("revert:merged1") < JOURNAL.index("deploy:revert111")


@pytest.mark.asyncio
async def test_dead_deploy_rolls_back_without_running_checks():
    result = await _run([_pr(1)], deploy_fails={"merged1"})
    assert result["failed"] == [1]
    assert "verify" not in JOURNAL
    assert "revert:merged1" in JOURNAL


@pytest.mark.asyncio
async def test_conflicted_pr_goes_to_developer_and_returns():
    conflicted = _pr(3, mergeable=False, mergeable_state="dirty")
    result = await _run([conflicted])

    assert "fix:3" in JOURNAL
    assert result["shipped"] == [3]
    assert result["delegated"] == []


@pytest.mark.asyncio
async def test_nothing_to_ship_creates_no_release_at_all():
    """Пустая очередь не оставляет следа в списке релизов.

    Черновик «ничего не отгружено» копился бы в репозитории с каждой
    проверочной командой; причины отказа уезжают комментарием.
    """
    result = await _run([_pr(4, approved=False)])
    assert result["planned"] == 0
    assert result["published"] is False
    assert result["skipped"] == [4]
    assert not [entry for entry in JOURNAL if entry.startswith("release:")]
    assert "publish:True" not in JOURNAL


@pytest.mark.asyncio
async def test_pending_mergeability_is_waited_out_not_skipped():
    """`mergeable=None` — «ответ ещё не готов», а не «конфликта нет».

    Живой прогон: после мержа соседа GitHub пересчитывает мержабельность, и PR
    #98 выпал из релиза с «ещё считает», хотя был готов.
    """
    unknown = _pr(1, mergeable=None, mergeable_state="unknown")
    ready = _pr(1)
    result = await _run([_pr(1)], facts_seq={1: [unknown, unknown, ready]})

    assert result["shipped"] == [1]
    assert JOURNAL.count("facts:1") >= 3   # два «ещё считаю» и один готовый


@pytest.mark.asyncio
async def test_conflict_appearing_at_step_time_goes_to_developer():
    """Конфликт от только что влитого соседа — повод позвать разработчика,
    а не пропустить шаг."""
    conflicted = _pr(2, mergeable=False, mergeable_state="dirty")
    result = await _run([_pr(1), _pr(2)],
                        facts_seq={2: [conflicted, _pr(2)]})

    assert "fix:2" in JOURNAL
    assert result["shipped"] == [1, 2]
