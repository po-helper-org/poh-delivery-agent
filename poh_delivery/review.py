"""Вердикт ревью: кто и когда сказал, что PR можно вливать.

Одобрение человека и вердикт ревью — разные вещи, и релиз обязан спрашивать оба.
Метка `ready-to-ship` означает «я хочу это в проде»; вердикт ревью означает «код
проверен и замечаний не осталось». До этого модуля релиз знал только первое, и
PR с незакрытыми замечаниями уезжал в прод, если человек поставил метку.

Вердикт собирается из того, что контур уже пишет в PR, а не из нового протокола:

- **ревью GitHub** — `CHANGES_REQUESTED` от человека закрывает мерж, `APPROVED`
  открывает;
- **метка `needs-human:pr`** — круг правок сдался, дальше нужен человек;
- **комментарии круга правок** — «✅ Круг правок завершён» (замечаний нет) и
  «⚠️ Доведение остановлено» (не сошлись);
- **комментарий PR-Agent** с коммитом, до которого ревью актуально.

Ключевое правило — **свежесть**: вердикт относится к КОММИТУ, а не к PR. Правка
после вердикта делает его недействительным, иначе «замечаний нет» из вчерашнего
круга разрешало бы влить сегодняшний код, которого ревьюер не видел.

Модуль чистый: ни сети, ни Temporal, ни GitHub.
"""

import re

# --- вердикты ---

CLEAN = "clean"        # проверено, замечаний нет — мерж открыт
CHANGES = "changes"    # ревьюер требует правок
BLOCKED = "blocked"    # круг правок сдался, ждут человека
STALE = "stale"        # вердикт есть, но он про прежний коммит
NONE = "none"          # ревью для этого PR не проводилось

# Маркеры контура. Держатся здесь, а не в Harness, потому что читает их
# Delivery-Agent; разъехавшись, они дадут «вердикта нет» на живом вердикте.
SETTLED_MARKER = "Круг правок завершён"
EXHAUSTED_MARKER = "Доведение остановлено"
NEEDS_HUMAN_LABEL = "needs-human:pr"

# Как PR-Agent помечает, до какого коммита ревью актуально. Живьём это чаще
# ССЫЛКА, а не голый sha: «(Review updated until commit
# https://github.com/o/r/commit/2ecbc5c…)». Разбор только голого sha молча
# считал ревью отсутствующим — и релиз крутил круги, прося ревью, которое уже
# было сделано.
_COMMIT_URL_RE = re.compile(r"/commit/([0-9a-f]{7,40})", re.IGNORECASE)
_REVIEW_SHA_RE = re.compile(r"commit\s+`?([0-9a-f]{7,40})", re.IGNORECASE)


def review_head(comments: list[dict]) -> str:
    """Коммит, до которого ревью актуально, по комментариям PR-Agent."""
    found = ""
    for comment in comments:
        body = comment.get("body") or ""
        if "review updated" not in body.lower():
            continue
        match = _COMMIT_URL_RE.search(body) or _REVIEW_SHA_RE.search(body)
        if match:
            found = match.group(1)
    return found


def _fresh(comment: dict, head_time: str) -> bool:
    """Комментарий появился ПОСЛЕ последнего коммита ветки.

    Сравнение строк ISO-8601 в UTC — не лень, а осознанный выбор: обе даты
    приходят из GitHub в одном формате (`2026-08-24T10:03:40Z`), и разбор их в
    datetime добавил бы ошибок ровно там, где ошибаться нельзя.
    """
    created = comment.get("created_at") or ""
    return bool(created) and bool(head_time) and created >= head_time


def verdict(head_sha: str, head_time: str, review_decision: str,
            labels: list[str], comments: list[dict]) -> tuple[str, str]:
    """Вердикт ревью и его причина одной строкой."""
    if review_decision == "CHANGES_REQUESTED":
        return CHANGES, "ревьюер запросил изменения"

    if NEEDS_HUMAN_LABEL in labels:
        return BLOCKED, f"метка {NEEDS_HUMAN_LABEL}: круг правок отдал PR человеку"

    # Итог круга правок — самый поздний из тех, что относятся к текущему коммиту.
    latest = ""
    for comment in comments:
        body = comment.get("body") or ""
        if SETTLED_MARKER in body or EXHAUSTED_MARKER in body:
            if _fresh(comment, head_time):
                latest = body
    if EXHAUSTED_MARKER in latest:
        return BLOCKED, "круг правок остановлен: замечания остались в силе"
    if SETTLED_MARKER in latest:
        return CLEAN, "круг правок завершён, замечаний к текущему коммиту нет"

    if review_decision == "APPROVED":
        approved_fresh = any(_fresh(c, head_time) for c in comments
                             if (c.get("kind") or "") == "review-approved")
        if approved_fresh or not comments:
            return CLEAN, "ревью GitHub: APPROVED"
        return STALE, "APPROVED относится к прежнему коммиту"

    seen = review_head(comments)
    if seen and head_sha and not head_sha.startswith(seen) and not seen.startswith(head_sha[:7]):
        return STALE, f"ревью актуально до {seen}, а в ветке уже {head_sha[:7]}"
    if seen:
        return STALE, "ревью проведено, но вердикта круга правок ещё нет"
    return NONE, "ревью по этому PR не проводилось"
