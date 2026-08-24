"""Воркфлоу релиза — семь шагов HowToDemo, записанные как долгоживущий процесс.

Почему Temporal, а не скрипт: релиз идёт минутами и десятками минут, между
шагами ждёт GitHub, разработчика и живой сервис, и обязан пережить рестарт
воркера, не потеряв, что уже влито. Скрипт, упавший между мержем и выкаткой,
оставляет систему в состоянии, о котором никто не знает; воркфлоу продолжится с
того же места, а его история — готовый журнал разбора.

Порядок шагов ровно тот, что в постановке:

1. Триггер — PR одобрен к merge (команда `/release`).
2. Снимок состояния репозитория и открытых PR.
3. Проверка актуальности; конфликтующие уходят агенту разработки.
4. Релизная очередь + черновик релиза с планом + ссылка во всех PR.
5. Пошаговая отгрузка с проверкой живой системы, при провале — откат.
6. Дозапись в релиз: что сделано, что изменилось для пользователей.
7. Завершение.
"""

from datetime import timedelta
from typing import List

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from poh_delivery import render, rules
    from poh_delivery.model import (
        CONFLICT,
        ELIGIBLE,
        CheckResult,
        ChecksBundle,
        DeliveryRequest,
        DeployResult,
        PullFacts,
        ReleaseRef,
        RepoState,
        StepOutcome,
        Verdict,
    )

# Очередь Harness: конфликты чинит агент разработки, который живёт там.
HARNESS_TASK_QUEUE = "issue-lifecycle"
MERGE_METHOD = "squash"
# Сколько раз отдавать один PR разработчику за релиз. Один: если круг правок не
# развёл конфликт с первого раза, второй круг на том же входе даст то же самое,
# а платный прогон агента — не то, что стоит повторять «на всякий случай».
CONFLICT_ROUNDS = 1
# Сколько ждать проверок после круга правок. Пуш в ветку перезапускает CI, и
# сразу после него у головного коммита проверок нет вовсе — «нет проверок» и
# «проверки прошли» тут неразличимы, а цена ошибки — влить непроверенное.
CONFLICT_CHECK_WAIT_MINUTES = 15
# Сколько ждать, пока GitHub досчитает мержабельность. Он считает её лениво и
# ПЕРЕсчитывает после каждого мержа в базу — то есть ровно тогда, когда релиз
# переходит к следующему шагу. На живом прогоне #98 из-за этого выпал из релиза
# с «ещё считает», хотя конфликта у него не было.
MERGEABILITY_WAIT_TRIES = 9
MERGEABILITY_WAIT_SECONDS = 20

_READ = RetryPolicy(maximum_attempts=3)
# Мутации не ретраятся вслепую: повторный мерж по уже влитому PR вернёт 405, а
# повторный revert создаст второй откат. Одна попытка, дальше — решение в
# воркфлоу.
_WRITE = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="DeliveryRelease")
class DeliveryRelease:
    @workflow.run
    async def run(self, request: DeliveryRequest) -> dict:
        info = workflow.info()
        repo = request.repo

        # --- Шаг 1-2: подтверждение приёма и снимок состояния ---
        if request.issue_number:
            await self._comment(repo, request.issue_number, (
                f"**Delivery-Agent взял релиз в работу.**\n\n"
                f"Temporal: `{info.workflow_id}` / `{info.run_id}`.\n"
                f"Сейчас соберу состояние репозитория и открытых PR."
            ))

        state: RepoState = await workflow.execute_activity(
            "delivery_collect_state", repo, result_type=RepoState,
            start_to_close_timeout=timedelta(minutes=10), retry_policy=_READ)

        bundle: ChecksBundle = await workflow.execute_activity(
            "delivery_read_checks", args=[repo, state.default_branch],
            result_type=ChecksBundle,
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_READ)

        # --- Шаг 3: актуальность и конфликты ---
        verdicts = [rules.classify(pull) for pull in state.pulls]
        by_number = {pull.number: pull for pull in state.pulls}

        eligible = [by_number[v.number] for v in verdicts if v.verdict == ELIGIBLE]
        conflicted = [v for v in verdicts if v.verdict == CONFLICT]
        skipped = [v for v in verdicts if v.verdict not in (ELIGIBLE, CONFLICT)]

        delegated: list[Verdict] = []
        for verdict in conflicted:
            fixed = await self._delegate_conflict(repo, verdict.number)
            if fixed is None:
                delegated.append(verdict)
                continue
            fresh_verdict = rules.classify(fixed)
            if fresh_verdict.verdict == ELIGIBLE:
                eligible.append(fixed)
                by_number[fixed.number] = fixed
            else:
                delegated.append(Verdict(verdict.number, CONFLICT,
                                         f"конфликт после круга правок: {fresh_verdict.reason}"))

        # --- Шаг 4: очередь, релиз, ссылка во всех PR ---
        tags: list[str] = await workflow.execute_activity(
            "delivery_existing_tags", repo, result_type=List[str],
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_READ)
        day = workflow.now().date().isoformat()
        plan = rules.build_plan(
            repo=repo, base_sha=state.base_sha, prs=eligible, checks=bundle.checks,
            tag=rules.next_tag(tags, day), checks_source=bundle.source,
            skipped=skipped, delegated=delegated)

        body = render.plan_md(plan, request.requested_by, info.run_id, info.workflow_id)
        release: ReleaseRef = await workflow.execute_activity(
            "delivery_create_release",
            args=[repo, plan.tag, plan.title, body, state.default_branch],
            result_type=ReleaseRef,
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_WRITE)

        for step in plan.steps:
            await self._comment(repo, step.pr_number,
                                render.pr_announcement(plan, release.url, step.order))

        if not plan.steps:
            # Пустая очередь — законный исход, а не сбой: одобренных PR может не
            # быть вовсе. Релиз при этом остаётся черновиком, чтобы не плодить
            # публикации «ничего не отгружено».
            await self._finish_empty(repo, request, plan, release, info)
            return self._summary(plan, [], release, published=False)

        # --- Шаг 5: отгрузка по одному шагу с проверкой живой системы ---
        outcomes: list[StepOutcome] = []
        bodies = {pull.number: pull.body for pull in by_number.values()}
        previous_sha = state.base_sha

        for step in plan.steps:
            fresh = await self._settled_facts(repo, step.pr_number)
            fresh_verdict = rules.classify(fresh)

            if fresh_verdict.verdict == CONFLICT:
                # Конфликт мог появиться от только что влитого соседа. Это не
                # повод бросать PR: агент разработки уже подключён к релизу,
                # и правильный ответ — отдать ветку ему, а не пропустить шаг.
                fixed = await self._delegate_conflict(repo, step.pr_number)
                if fixed is not None:
                    fresh = fixed
                    fresh_verdict = rules.classify(fresh)

            if fresh_verdict.verdict != ELIGIBLE:
                # Состояние успело измениться между планом и шагом — законный
                # случай, а не авария: сосед по очереди мог тронуть те же файлы.
                # Пропускаем шаг, релиз продолжается.
                outcomes.append(StepOutcome(
                    pr_number=step.pr_number, ok=False,
                    detail=f"на момент шага PR уже не годен: {fresh_verdict.reason}"))
                continue

            merged_sha = await workflow.execute_activity(
                "delivery_merge", args=[repo, step.pr_number, MERGE_METHOD],
                start_to_close_timeout=timedelta(minutes=5), retry_policy=_WRITE)

            deployed: DeployResult = await workflow.execute_activity(
                "delivery_deploy", args=[repo, merged_sha, bundle.service],
                result_type=DeployResult,
                start_to_close_timeout=timedelta(minutes=15), retry_policy=_WRITE)

            checks = []
            if deployed.ok:
                checks = await workflow.execute_activity(
                    "delivery_verify", args=[bundle.checks, bundle.service],
                    result_type=List[CheckResult],
                    start_to_close_timeout=timedelta(minutes=10), retry_policy=_READ)

            failed = [c for c in checks if not c.ok]
            if deployed.ok and not failed:
                outcomes.append(StepOutcome(
                    pr_number=step.pr_number, ok=True, merged_sha=merged_sha,
                    deployed=True, checks=checks,
                    detail=deployed.detail))
                previous_sha = merged_sha
                await self._comment(repo, step.pr_number, (
                    f"**Delivery-Agent: шаг {step.order} отгружен.**\n\n"
                    f"Влит в `{plan.repo}` как `{merged_sha[:12]}`, выкачен на прод-контур, "
                    f"проверки зелёные ({len(checks)} шт.). Релиз: {release.url}"))
                continue

            # --- Провал: откат и остановка релиза ---
            reason = deployed.detail if not deployed.ok else "; ".join(
                f"{c.name}: {c.detail}" for c in failed)
            rolled = await self._rollback(repo, merged_sha, state.default_branch,
                                          previous_sha, bundle)
            outcomes.append(StepOutcome(
                pr_number=step.pr_number, ok=False, merged_sha=merged_sha,
                deployed=deployed.ok, checks=checks, rolled_back=rolled,
                detail=reason + ("; изменение откачено" if rolled
                                 else "; ОТКАТ НЕ УДАЛСЯ — нужна рука человека")))
            await self._comment(repo, step.pr_number, (
                f"**Delivery-Agent: шаг {step.order} отменён.**\n\n"
                f"Причина: {reason}\n\n"
                + ("Изменение откачено, база возвращена в прежнее состояние, "
                   "релиз остановлен." if rolled else
                   "**Откат не удался** — состояние базы требует ручного разбора.")
                + f"\n\nРелиз: {release.url}"))
            break

        # --- Шаг 6-7: отчёт в релиз и завершение ---
        report = render.plan_md(plan, request.requested_by, info.run_id, info.workflow_id)
        report += "\n---\n\n" + render.report_md(
            plan, outcomes, bodies, info.run_id, info.workflow_id,
            workflow.now().isoformat(timespec="seconds"))
        await workflow.execute_activity(
            "delivery_update_release", args=[repo, release.release_id, report, True],
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_WRITE)

        if request.issue_number:
            await self._comment(repo, request.issue_number,
                                render.summary_comment(plan, outcomes, release.url))

        return self._summary(plan, outcomes, release, published=True)

    # --- вспомогательное ---

    async def _settled_facts(self, repo: str, number: int) -> PullFacts:
        """Факты PR, дождавшись, пока GitHub досчитает мержабельность.

        `mergeable=None` — не «конфликта нет» и не «конфликт есть», а «ответ
        ещё не готов». Решать по нему нельзя, а пропускать шаг из-за него —
        значит терять готовый PR на ровном месте.
        """
        facts: PullFacts = await workflow.execute_activity(
            "delivery_pull_facts", args=[repo, number], result_type=PullFacts,
            start_to_close_timeout=timedelta(minutes=3), retry_policy=_READ)
        tries = 0
        while facts.mergeable is None and tries < MERGEABILITY_WAIT_TRIES:
            await workflow.sleep(timedelta(seconds=MERGEABILITY_WAIT_SECONDS))
            tries += 1
            facts = await workflow.execute_activity(
                "delivery_pull_facts", args=[repo, number], result_type=PullFacts,
                start_to_close_timeout=timedelta(minutes=3), retry_policy=_READ)
        return facts

    async def _comment(self, repo: str, number: int, body: str) -> None:
        await workflow.execute_activity(
            "delivery_comment", args=[repo, number, body],
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_READ)

    async def _delegate_conflict(self, repo: str, number: int) -> PullFacts | None:
        """Отдать конфликтующий PR агенту разработки Harness и пересмотреть факты.

        Агент разработки живёт в Harness и запускается его же активностью —
        Delivery-Agent не поднимает второго. `None` означает, что круг правок
        не состоялся: PR остаётся в релизе только через человека.
        """
        for _ in range(CONFLICT_ROUNDS):
            try:
                await workflow.execute_activity(
                    "delivery_fix_conflicts", args=[repo, number],
                    task_queue=HARNESS_TASK_QUEUE,
                    start_to_close_timeout=timedelta(minutes=60),
                    heartbeat_timeout=timedelta(minutes=10),
                    retry_policy=_WRITE)
            except Exception:
                return None

            facts: PullFacts = await workflow.execute_activity(
                "delivery_pull_facts", args=[repo, number], result_type=PullFacts,
                start_to_close_timeout=timedelta(minutes=3), retry_policy=_READ)
            waited = 0
            while facts.checks_state in ("pending", "none") and waited < CONFLICT_CHECK_WAIT_MINUTES:
                await workflow.sleep(timedelta(minutes=1))
                waited += 1
                facts = await workflow.execute_activity(
                    "delivery_pull_facts", args=[repo, number], result_type=PullFacts,
                    start_to_close_timeout=timedelta(minutes=3), retry_policy=_READ)
            return facts
        return None

    async def _rollback(self, repo: str, merge_sha: str, branch: str,
                        previous_sha: str, bundle: ChecksBundle) -> bool:
        """Отмена шага: revert в базовой ветке и возврат прежней сборки на прод."""
        try:
            revert_sha = await workflow.execute_activity(
                "delivery_revert", args=[repo, merge_sha, branch],
                start_to_close_timeout=timedelta(minutes=10), retry_policy=_WRITE)
        except Exception:
            return False
        target = revert_sha or previous_sha
        try:
            result: DeployResult = await workflow.execute_activity(
                "delivery_deploy", args=[repo, target, bundle.service],
                result_type=DeployResult,
                start_to_close_timeout=timedelta(minutes=15), retry_policy=_WRITE)
        except Exception:
            return False
        return bool(result.ok)

    async def _finish_empty(self, repo: str, request: DeliveryRequest, plan,
                            release: ReleaseRef, info) -> None:
        note = ("**Delivery-Agent: отгружать нечего.**\n\n"
                "Ни один открытый PR не прошёл отбор.\n\n"
                + (render.verdict_lines(plan.skipped + plan.delegated) or "Открытых PR нет.")
                + f"\n\nЧерновик релиза с разбором: {release.url}")
        if request.issue_number:
            await self._comment(repo, request.issue_number, note)

    def _summary(self, plan, outcomes: list[StepOutcome], release: ReleaseRef,
                 published: bool) -> dict:
        return {
            "tag": plan.tag,
            "release_url": release.url,
            "published": published,
            "planned": len(plan.steps),
            "shipped": [o.pr_number for o in outcomes if o.ok],
            "failed": [o.pr_number for o in outcomes if not o.ok],
            "delegated": [v.number for v in plan.delegated],
            "skipped": [v.number for v in plan.skipped],
        }
