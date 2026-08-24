"""Правила отбора и очереди — ядро агента, тестируется без окружения."""

from poh_delivery.model import (
    CHECKS_PENDING,
    CHECKS_RED,
    CONFLICT,
    DRAFT,
    ELIGIBLE,
    NOT_APPROVED,
    CheckSpec,
    PullFacts,
)
from poh_delivery import rules


def pr(number=1, **kwargs) -> PullFacts:
    base = dict(number=number, title=f"PR {number}", approved=True, mergeable=True,
                mergeable_state="clean", checks_state="success", files=["src/a.mjs"],
                additions=10, deletions=1)
    base.update(kwargs)
    return PullFacts(**base)


def test_approved_clean_pr_is_eligible():
    assert rules.classify(pr()).verdict == ELIGIBLE


def test_label_replaces_review_approval():
    verdict = rules.classify(pr(approved=False, labels=["ready-to-ship"]))
    assert verdict.verdict == ELIGIBLE


def test_unapproved_pr_never_ships():
    assert rules.classify(pr(approved=False)).verdict == NOT_APPROVED


def test_conflict_wins_over_red_checks():
    """Красный CI на конфликтующей ветке — следствие; чинить надо конфликт."""
    verdict = rules.classify(pr(mergeable=False, mergeable_state="dirty",
                                checks_state="failure"))
    assert verdict.verdict == CONFLICT


def test_red_checks_block():
    assert rules.classify(pr(checks_state="failure")).verdict == CHECKS_RED


def test_pending_checks_block():
    assert rules.classify(pr(checks_state="pending")).verdict == CHECKS_PENDING


def test_unknown_mergeability_is_not_permission():
    verdict = rules.classify(pr(mergeable=None, mergeable_state="unknown"))
    assert verdict.verdict == CHECKS_PENDING


def test_draft_never_ships():
    assert rules.classify(pr(draft=True)).verdict == DRAFT


def test_behind_branch_still_ships():
    verdict = rules.classify(pr(mergeable=True, mergeable_state="behind"))
    assert verdict.verdict == ELIGIBLE


def test_risk_grows_with_volume():
    assert rules.risk_of(pr(files=["a"], additions=1)) == "low"
    assert rules.risk_of(pr(files=["a", "b", "c"], additions=1)) == "medium"
    assert rules.risk_of(pr(files=["a"], additions=500)) == "high"


def test_cheap_changes_go_first():
    heavy = pr(2, files=[f"f{i}" for i in range(8)], additions=400)
    light = pr(7, files=["src/a.mjs"], additions=5)
    order = [p.number for p in rules.order_steps([heavy, light])]
    assert order == [7, 2]


def test_equal_risk_keeps_arrival_order():
    order = [p.number for p in rules.order_steps([pr(9), pr(3)])]
    assert order == [3, 9]


def test_shared_files_create_dependency():
    first = pr(3, files=["src/pricing.mjs"])
    second = pr(5, files=["src/pricing.mjs", "src/server.mjs"])
    plan = rules.build_plan("o/r", "base", [second, first], [], tag="release-2026-08-21.1")
    step_five = next(s for s in plan.steps if s.pr_number == 5)
    assert step_five.depends_on == [3]
    assert "общие файлы" in step_five.reason


def test_plan_carries_checks_names():
    checks = [CheckSpec(name="quote-base"), CheckSpec(name="health")]
    plan = rules.build_plan("o/r", "base", [pr(1)], checks, tag="t")
    assert plan.steps[0].checks == ["quote-base", "health"]


def test_next_tag_counts_within_day():
    assert rules.next_tag([], "2026-08-21") == "release-2026-08-21.1"
    assert rules.next_tag(["release-2026-08-21.1"], "2026-08-21") == "release-2026-08-21.2"
    assert rules.next_tag(["release-2026-08-20.9"], "2026-08-21") == "release-2026-08-21.1"
