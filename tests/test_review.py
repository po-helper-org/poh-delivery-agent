"""Вердикт ревью: что считается разрешением влить, а что — нет.

Главное свойство — свежесть. Вердикт относится к КОММИТУ: «замечаний нет» из
вчерашнего круга не разрешает влить сегодняшний код, которого ревьюер не видел.
"""

from poh_delivery import review

HEAD = "abc1234def5678"
HEAD_TIME = "2026-08-24T10:00:00Z"


def note(body="", created="2026-08-24T11:00:00Z", kind="comment", author="poh-harness-demo"):
    return {"author": author, "created_at": created, "body": body, "kind": kind}


def test_settled_round_after_head_commit_opens_merge():
    result, reason = review.verdict(
        HEAD, HEAD_TIME, "COMMENTED", [],
        [note("✅ Круг правок завершён: агент не нашёл в ревью того, что требует изменений")])
    assert result == review.CLEAN
    assert "замечаний" in reason


def test_settled_round_before_head_commit_is_stale():
    """Правка после вердикта делает вердикт недействительным."""
    result, _ = review.verdict(
        HEAD, HEAD_TIME, "COMMENTED", [],
        [note("✅ Круг правок завершён", created="2026-08-24T09:00:00Z"),
         note("Review updated until commit 0000000", created="2026-08-24T09:05:00Z")])
    assert result == review.STALE


def test_exhausted_round_blocks():
    result, reason = review.verdict(
        HEAD, HEAD_TIME, "COMMENTED", [],
        [note("⚠️ Доведение остановлено: пройдено 3 кругов правок")])
    assert result == review.BLOCKED
    assert "замечания" in reason


def test_needs_human_label_blocks_even_with_settled_comment():
    """Метка очереди к человеку сильнее любого автоматического вердикта."""
    result, reason = review.verdict(
        HEAD, HEAD_TIME, "COMMENTED", ["needs-human:pr"],
        [note("✅ Круг правок завершён")])
    assert result == review.BLOCKED
    assert "needs-human:pr" in reason


def test_changes_requested_beats_everything():
    result, _ = review.verdict(
        HEAD, HEAD_TIME, "CHANGES_REQUESTED", [], [note("✅ Круг правок завершён")])
    assert result == review.CHANGES


def test_approved_review_after_head_is_clean():
    result, _ = review.verdict(
        HEAD, HEAD_TIME, "APPROVED", [],
        [note(kind="review-approved", created="2026-08-24T10:30:00Z")])
    assert result == review.CLEAN


def test_approved_review_before_head_is_stale():
    result, _ = review.verdict(
        HEAD, HEAD_TIME, "APPROVED", [],
        [note(kind="review-approved", created="2026-08-24T08:00:00Z")])
    assert result == review.STALE


def test_review_of_older_commit_is_stale():
    result, reason = review.verdict(
        HEAD, HEAD_TIME, "COMMENTED", [],
        [note("Persistent review updated to latest commit 9999999")])
    assert result == review.STALE
    assert "9999999" in reason


def test_no_review_at_all():
    result, _ = review.verdict(HEAD, HEAD_TIME, "REVIEW_REQUIRED", [], [])
    assert result == review.NONE


def test_review_head_takes_the_last_mention():
    head = review.review_head([
        note("Review updated until commit 1111111"),
        note("Persistent review updated to latest commit 2222222"),
    ])
    assert head == "2222222"


def test_review_head_reads_a_commit_link():
    """PR-Agent пишет ссылку, а не голый sha — живой формат на poh-demo-checkout.

    Разбор только голого sha считал ревью отсутствующим: релиз крутил круги,
    прося ревью, которое уже было сделано, и упирался в потолок кругов.
    """
    body = ("## PR Reviewer Guide 🔍\n\n#### (Review updated until commit "
            "https://github.com/po-helper-org/poh-demo-checkout/commit/"
            "2ecbc5c541e14527dd460747f7d57d416736dd76)")
    assert review.review_head([note(body)]) == "2ecbc5c541e14527dd460747f7d57d416736dd76"


def test_review_of_current_head_by_link_is_not_stale():
    body = ("#### (Review updated until commit "
            "https://github.com/o/r/commit/abc1234def5678)")
    result, _ = review.verdict(HEAD, HEAD_TIME, "COMMENTED", [], [note(body)])
    assert result == review.STALE     # ревью есть, но вердикта круга ещё нет
    assert review.review_head([note(body)]).startswith("abc1234")
