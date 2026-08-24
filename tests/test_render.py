"""Тексты релиза: план обязан нести то, по чему потом разбирают выкатку."""

from poh_delivery import render, rules
from poh_delivery.model import CheckResult, CheckSpec, PullFacts, StepOutcome, Verdict


def _plan():
    prs = [PullFacts(number=6, title="Промокод", files=["src/pricing.mjs"], additions=30),
           PullFacts(number=9, title="Цвета", files=["src/ui.mjs"], additions=400)]
    checks = [CheckSpec(name="quote-base", path="/quote", method="POST",
                        expect_json={"total": 2300}, source="БФТ #1, HowToDemo п.3")]
    return rules.build_plan("o/r", "base1234567890", prs, checks, tag="release-2026-08-21.1",
                            checks_source=".delivery/checks.json@main",
                            skipped=[Verdict(11, "not-approved", "нет одобрения")],
                            delegated=[Verdict(12, "conflict", "конфликт с базой")])


def test_plan_lists_queue_checks_and_rollback():
    body = render.plan_md(_plan(), "kibarik", "run-1", "delivery-o/r")
    assert "#6" in body and "#9" in body
    assert "quote-base" in body and "БФТ #1" in body
    assert "revert" in body.lower() or "откат" in body
    assert "delivery-o/r" in body and "run-1" in body


def test_plan_names_what_did_not_make_it_and_why():
    body = render.plan_md(_plan(), "kibarik", "run-1", "wf")
    assert "#11" in body and "нет одобрения" in body
    assert "#12" in body and "конфликт" in body


def test_announcement_carries_release_link_and_position():
    plan = _plan()
    text = render.pr_announcement(plan, "https://example/r/1", step_order=2)
    assert "https://example/r/1" in text
    assert "шаг 2 из 2" in text


def test_report_separates_shipped_from_rolled_back():
    plan = _plan()
    outcomes = [
        StepOutcome(pr_number=6, ok=True, merged_sha="abcdef1234567",
                    checks=[CheckResult("quote-base", True, "HTTP 200")]),
        StepOutcome(pr_number=9, ok=False, merged_sha="ff00ff00ff00", rolled_back=True,
                    checks=[CheckResult("quote-base", False, "пришёл 500")],
                    detail="quote-base: пришёл 500"),
    ]
    body = render.report_md(plan, outcomes, {6: "Closes #1"}, "run-1", "wf", "2026-08-21T12:00:00")
    assert "✅ #6" in body and "⛔ #9" in body
    assert "задача #1" in body            # что изменилось для пользователей
    assert "Что пошло не так" in body
    assert "Изменение отменено" in body


def test_report_marks_steps_that_never_ran():
    plan = _plan()
    outcomes = [StepOutcome(pr_number=6, ok=False, detail="проверка красная", rolled_back=True)]
    body = render.report_md(plan, outcomes, {}, "run-1", "wf", "2026-08-21T12:00:00")
    assert "#9 — не дошёл" in body


def test_linked_issue_is_read_from_pr_body():
    assert render.linked_issue("Closes #42") == 42
    assert render.linked_issue("resolves #7 в тексте") == 7
    assert render.linked_issue("ничего") is None
