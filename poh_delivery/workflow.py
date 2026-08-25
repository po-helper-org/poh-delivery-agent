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
    from poh_delivery import render, review, rules
    from poh_delivery.model import (
        CONFLICT,
        ELIGIBLE,
        REVIEW_BLOCKED,
        REVIEW_PENDING,
        CheckResult,
        ChecksBundle,
        DeliveryRequest,
        DeployResult,
        ObservationResult,
        PullFacts,
        ReleaseRef,
        RepoState,
        ReviewRound,
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
# Сколько кругов «ревью → правки» гонять по одному PR за релиз. Три — потолок
# протокола для доведения PR: агент, который третий раз не сходится с ревью,
# дальше не сойдётся, он спорит, а не исправляет. Такой PR уходит человеку.
REVIEW_ROUNDS = 3
# Ожидание ревью — НЕ круг правок. Пока PR-Agent считает, никто ничего не
# исправлял, и тратить на это потолок кругов значит отдать человеку PR, к
# которому никто не предъявил ни одного замечания.
REVIEW_WAIT_TRIES = 6
# Пауза перед перечитыванием состояния: PR-Agent отвечает минутами, и опрашивать
# его чаще — только жечь лимиты GitHub.
REVIEW_POLL_SECONDS = 90
# После «замечаний нет» итог круга публикуется комментарием — вердикт появится
# через секунды, а не минуты.
VERDICT_SETTLE_SECONDS = 20

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
        needs_review = [v for v in verdicts if v.verdict == REVIEW_PENDING]
        skipped = [v for v in verdicts if v.verdict not in (ELIGIBLE, CONFLICT, REVIEW_PENDING)]

        delegated: list[Verdict] = []
        for verdict in conflicted:
            fixed = await self._delegate_conflict(repo, verdict.number)
            if fixed is None:
                delegated.append(verdict)
                continue
            fresh_verdict = rules.classify(fixed)
            if fresh_verdict.verdict == REVIEW_PENDING:
                # Ветку тронул агент разработки — прежний вердикт ревью к ней
                # не относится. Замыкаем цикл: разработчик → ревью → вердикт.
                fixed = await self._review_gate(repo, verdict.number, fixed)
                fresh_verdict = rules.classify(fixed)
            if fresh_verdict.verdict == ELIGIBLE:
                eligible.append(fixed)
                by_number[fixed.number] = fixed
            else:
                delegated.append(Verdict(verdict.number, fresh_verdict.verdict,
                                         f"после круга правок: {fresh_verdict.reason}"))

        for verdict in needs_review:
            reviewed = await self._review_gate(repo, verdict.number, by_number[verdict.number])
            by_number[verdict.number] = reviewed
            fresh_verdict = rules.classify(reviewed)
            if fresh_verdict.verdict == ELIGIBLE:
                eligible.append(reviewed)
            else:
                skipped.append(fresh_verdict)

        # --- Шаг 4: очередь, релиз, ссылка во всех PR ---
        tags: list[str] = await workflow.execute_activity(
            "delivery_existing_tags", repo, result_type=List[str],
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_READ)
        day = workflow.now().date().isoformat()
        plan = rules.build_plan(
            repo=repo, base_sha=state.base_sha, prs=eligible, checks=bundle.checks,
            tag=rules.next_tag(tags, day), checks_source=bundle.source,
            skipped=skipped, delegated=delegated)

        if not plan.steps:
            # Пустая очередь — законный исход, а не сбой: одобренных PR может не
            # быть вовсе. Релиз при этом НЕ создаётся: черновик «ничего не
            # отгружено» — мусор, который копится в репозитории с каждой
            # проверочной командой, а разбор причин уезжает комментарием.
            await self._finish_empty(repo, request, plan)
            return self._summary(plan, [], ReleaseRef(), published=False)

        # Накопленный опыт организации — в план, который читает человек перед
        # одобрением выкатки. Слой не подключён — блок пуст, и документ
        # собирается ровно как раньше.
        org_rules, org_ids = await self._org_rules(repo)

        body = render.plan_md(plan, request.requested_by, info.run_id,
                              info.workflow_id, org_rules=org_rules)
        release: ReleaseRef = await workflow.execute_activity(
            "delivery_create_release",
            args=[repo, plan.tag, plan.title, body, state.default_branch],
            result_type=ReleaseRef,
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_WRITE)

        for step in plan.steps:
            await self._comment(repo, step.pr_number,
                                render.pr_announcement(plan, release.url, step.order))

        # --- Шаг 5: отгрузка по одному шагу с проверкой живой системы ---
        outcomes: list[StepOutcome] = []
        bodies = {pull.number: pull.body for pull in by_number.values()}
        previous_sha = state.base_sha

        for step in plan.steps:
            fresh = await self._settled_facts(repo, step.pr_number)
            fresh_verdict = rules.classify(fresh)

            if fresh_verdict.verdict == REVIEW_PENDING:
                # Вердикт мог устареть между планом и шагом: сосед по очереди
                # влит, ветка обновлена, ревьюер этого кода не видел.
                fresh = await self._review_gate(repo, step.pr_number, fresh)
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

            # Проверки перечитываются из ВЛИТОГО состояния, а не из того, что
            # было на старте релиза. PR, который меняет поведение, обязан менять
            # и `.delivery/checks.json`; читая старый файл, релиз проверял бы
            # свежий код вчерашним контрактом и откатывал сам себя. Живой
            # случай: `/healthz` стал отдавать `status`, проверка ждала `ok`.
            merged_bundle: ChecksBundle = await workflow.execute_activity(
                "delivery_read_checks", args=[repo, merged_sha],
                result_type=ChecksBundle,
                start_to_close_timeout=timedelta(minutes=2), retry_policy=_READ)
            if merged_bundle.checks or merged_bundle.service:
                bundle = merged_bundle

            deployed: DeployResult = await workflow.execute_activity(
                "delivery_deploy", args=[repo, merged_sha, bundle.service],
                result_type=DeployResult,
                start_to_close_timeout=timedelta(minutes=15), retry_policy=_WRITE)

            # Порядок проверок: выкатка → прогон проверок → окно наблюдения → повторный прогон проверок
            checks = []
            if deployed.ok and bundle.checks:
                # Первый прогон проверок: сразу после выкатки, чтобы явно сломанное изменение откатить быстро
                checks = await workflow.execute_activity(
                    "delivery_verify", args=[bundle.checks, bundle.service],
                    result_type=List[CheckResult],
                    start_to_close_timeout=timedelta(minutes=10), retry_policy=_READ)

            observation: ObservationResult | None = None
            if deployed.ok and not [c for c in checks if not c.ok]:
                # Если первый прогон прошёл, запускаем окно наблюдения
                observe_seconds = await workflow.execute_activity(
                    "delivery_get_observe_seconds", result_type=int,
                    start_to_close_timeout=timedelta(minutes=1), retry_policy=_READ)
                if observe_seconds > 0:
                    observation = await workflow.execute_activity(
                        "delivery_observe", args=[observe_seconds, bundle.service],
                        result_type=ObservationResult,
                        start_to_close_timeout=timedelta(minutes=20), retry_policy=_READ)

            # Повторный прогон проверок после окна наблюдения
            final_checks = []
            if deployed.ok and not [c for c in checks if not c.ok] and (observation is None or observation.alive):
                if bundle.checks:
                    final_checks = await workflow.execute_activity(
                        "delivery_verify", args=[bundle.checks, bundle.service],
                        result_type=List[CheckResult],
                        start_to_close_timeout=timedelta(minutes=10), retry_policy=_READ)
            else:
                final_checks = checks  # Если окно не было или провалилось, используем первый прогон

            # Проверяем итоговый результат после всех этапов
            failed = [c for c in final_checks if not c.ok]
            observation_failed = observation is not None and not observation.alive
            
            if deployed.ok and not failed and not observation_failed:
                outcomes.append(StepOutcome(
                    pr_number=step.pr_number, ok=True, merged_sha=merged_sha,
                    deployed=True, checks=final_checks,
                    detail=deployed.detail, observation=observation))
                previous_sha = merged_sha
                obs_note = f", окно наблюдения {observation.duration}с прожито" if observation else ""
                await self._comment(repo, step.pr_number, (
                    f"**Delivery-Agent: шаг {step.order} отгружен.**\n\n"
                    f"Влит в `{plan.repo}` как `{merged_sha[:12]}`, выкачен на прод-контур{obs_note}, "
                    f"проверки зелёные ({len(final_checks)} шт.). Релиз: {release.url}"))
                continue

            # --- Провал: откат и остановка релиза ---
            if not deployed.ok:
                reason = deployed.detail
            elif observation_failed and observation:
                reason = f"сервис не пережил окно наблюдения: {observation.detail}"
            else:
                reason = "; ".join(f"{c.name}: {c.detail}" for c in failed)
            
            rolled = await self._rollback(repo, merged_sha, state.default_branch,
                                          previous_sha, bundle)
            outcomes.append(StepOutcome(
                pr_number=step.pr_number, ok=False, merged_sha=merged_sha,
                deployed=deployed.ok, checks=final_checks, rolled_back=rolled,
                detail=reason + ("; изменение откачено" if rolled
                                 else "; ОТКАТ НЕ УДАЛСЯ — нужна рука человека"),
                observation=observation))
            await self._comment(repo, step.pr_number, (
                f"**Delivery-Agent: шаг {step.order} отменён.**\n\n"
                f"Причина: {reason}\n\n"
                + ("Изменение откачено, база возвращена в прежнее состояние, "
                   "релиз остановлен." if rolled else
                   "**Откат не удался** — состояние базы требует ручного разбора.")
                + f"\n\nРелиз: {release.url}"))
            break

        # --- Шаг 6-7: отчёт в релиз и завершение ---
        report = render.plan_md(plan, request.requested_by, info.run_id,
                                info.workflow_id, org_rules=org_rules)
        report += "\n---\n\n" + render.report_md(
            plan, outcomes, bodies, info.run_id, info.workflow_id,
            workflow.now().isoformat(timespec="seconds"))
        await workflow.execute_activity(
            "delivery_update_release", args=[repo, release.release_id, report, True],
            start_to_close_timeout=timedelta(minutes=2), retry_policy=_WRITE)

        if request.issue_number:
            await self._comment(repo, request.issue_number,
                                render.summary_comment(plan, outcomes, release.url))

        await self._capture(repo, request, plan, outcomes, org_ids, info)
        return self._summary(plan, outcomes, release, published=True)

    async def _org_rules(self, repo: str) -> tuple[str, list]:
        """Правила организации от слоя саморефлексии.

        Слой ОПЦИОНАЛЕН, и его отказ не имеет права стоить релиза. Отдельный
        повод для перехвата: активность может быть не зарегистрирована вовсе —
        Harness подключает Delivery-Agent как внешний модуль, и его сборка
        может быть старше этой.
        """
        try:
            org: dict = await workflow.execute_activity(
                "delivery_memory_rules", repo, result_type=dict,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1))
        except Exception as e:                           # noqa: BLE001 — см. докстроку
            workflow.logger.warning("правила организации недоступны: %s", e)
            return "", []
        return org.get("text") or "", org.get("ids") or []

    async def _capture(self, repo, request, plan, outcomes, org_ids, info) -> None:
        """Запись об итерации поставки — слою саморефлексии.

        Отказ слоя не имеет права стоить релиза: он уже выкачен, а запись —
        побочный результат. Поэтому исключение гасится здесь и не поднимается
        в воркфлоу.
        """
        ok_steps = sum(1 for o in outcomes if getattr(o, "ok", False))
        episode = {
            "run_id": info.workflow_id,
            "repo": repo,
            "issue": request.issue_number or 0,
            "phase": "delivery",
            "agent": "delivery",
            "finished_at": workflow.now().isoformat(timespec="seconds"),
            "intent": f"отгрузить {len(plan.steps)} PR тегом {plan.tag}",
            "rules_injected": org_ids,
            "artifacts": {
                "steps": len(plan.steps),
                "steps_ok": ok_steps,
                "rolled_back": len(outcomes) - ok_steps,
                "delegated": len(plan.delegated),
                "skipped": len(plan.skipped),
            },
        }
        try:
            await workflow.execute_activity(
                "delivery_capture_episode", episode, result_type=bool,
                start_to_close_timeout=timedelta(seconds=30), retry_policy=_READ)
        except Exception as e:                           # noqa: BLE001 — см. докстроку
            workflow.logger.warning("запись об итерации поставки не отдана: %s", e)

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

            return await self._wait_for_checks(repo, number)
        return None

    async def _wait_for_checks(self, repo: str, number: int) -> PullFacts:
        """Дождаться, пока проверки нового коммита станут определёнными.

        Пуш в ветку перезапускает CI, и сразу после него у головного коммита
        проверок нет вовсе: «нет проверок» и «проверки прошли» в этот момент
        неразличимы, а цена ошибки — влить непроверенное.
        """
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

    async def _review_gate(self, repo: str, number: int, facts: PullFacts) -> PullFacts:
        """Круг «ревью → правки», пока не появится вердикт по текущему коммиту.

        Это и есть блокирующее ревью: пока вердикта нет, PR в очередь не
        попадает, а с вердиктом «замечания в силе» не попадает вовсе. Работу
        внутри круга делает агент разработки Harness — тот же, что чинит
        конфликты; Delivery-Agent только держит цикл и считает круги.
        """
        rounds = 0
        waits = 0
        while rounds < REVIEW_ROUNDS and waits <= REVIEW_WAIT_TRIES:
            try:
                result: ReviewRound = await workflow.execute_activity(
                    "delivery_review_round", args=[repo, number, rounds + 1],
                    task_queue=HARNESS_TASK_QUEUE, result_type=ReviewRound,
                    start_to_close_timeout=timedelta(minutes=60),
                    heartbeat_timeout=timedelta(minutes=10), retry_policy=_WRITE)
            except Exception:
                # Круг сорвался — вердикта нет, значит и мержа нет. Молчаливого
                # «поехали дальше» здесь быть не должно.
                return await self._settled_facts(repo, number)

            if result.changed:
                rounds += 1
                # Правки внесены, ревью запрошено — ждём CI по новому коммиту,
                # иначе следующий круг будет читать ревью прежнего кода.
                facts = await self._wait_for_checks(repo, number)
            elif result.settled:
                rounds += 1
                await workflow.sleep(timedelta(seconds=VERDICT_SETTLE_SECONDS))
                facts = await self._settled_facts(repo, number)
            else:
                # Ревью ещё нет — это ожидание, а не круг правок.
                waits += 1
                await workflow.sleep(timedelta(seconds=REVIEW_POLL_SECONDS))
                facts = await self._settled_facts(repo, number)

            if facts.review_verdict in (review.CLEAN, review.BLOCKED, review.CHANGES):
                return facts

        # Круги кончились, а вердикта нет: PR уходит человеку с меткой и итогом.
        await workflow.execute_activity(
            "delivery_review_exhausted", args=[repo, number, REVIEW_ROUNDS],
            task_queue=HARNESS_TASK_QUEUE,
            start_to_close_timeout=timedelta(minutes=5), retry_policy=_WRITE)
        return await self._settled_facts(repo, number)

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

    async def _finish_empty(self, repo: str, request: DeliveryRequest, plan) -> None:
        note = ("**Delivery-Agent: отгружать нечего.**\n\n"
                "Ни один открытый PR не прошёл отбор, релиз не заводился.\n\n"
                + (render.verdict_lines(plan.skipped + plan.delegated) or "Открытых PR нет."))
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
