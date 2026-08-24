"""Реализация GitHub-порта поверх REST. Токен приходит извне.

Свой клиент, а не импорт клиента Harness: модуль обязан собираться и
тестироваться в своём репозитории, где Harness не установлен. Единственное, что
приходит снаружи, — функция выдачи токена на репозиторий: в Harness это
installation-токен GitHub App, локально — PAT из окружения.
"""

import base64
import logging
import os
import shlex
import subprocess
import tempfile
import time
from typing import Callable

import requests

from poh_delivery import review as review_module
from poh_delivery.model import PullFacts

_log = logging.getLogger(__name__)

API = os.environ.get("GITHUB_API", "https://api.github.com")
_TIMEOUT = 30
# GitHub считает мержабельность ЛЕНИВО и пересчитывает её после каждого мержа в
# базу: первый запрос возвращает `mergeable: null` и запускает расчёт, ответ
# приходит через секунды. Документация прямо предписывает перезапрашивать.
_MERGEABLE_TRIES = int(os.environ.get("DELIVERY_MERGEABLE_TRIES", "8"))
_MERGEABLE_PAUSE = float(os.environ.get("DELIVERY_MERGEABLE_PAUSE", "3"))


def env_token_provider(repo: str) -> str:
    """Умолчание вне Harness: токен из окружения."""
    token = os.environ.get("DELIVERY_GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("нет токена: задай DELIVERY_GITHUB_TOKEN или GH_TOKEN")
    return token


class GitHubApi:
    def __init__(self, token_provider: Callable[[str], str] = env_token_provider,
                 dry_run: bool = False):
        self._token_for = token_provider
        self._dry_run = dry_run

    # --- транспорт ---

    def _headers(self, repo: str) -> dict:
        return {
            "Authorization": f"Bearer {self._token_for(repo)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, repo: str, path: str, **params) -> dict | list:
        response = requests.get(f"{API}{path}", headers=self._headers(repo),
                                params=params or None, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _write(self, method: str, repo: str, path: str, payload: dict) -> dict:
        if self._dry_run:
            _log.info("[DRY_RUN] %s %s %s", method, path, payload)
            return {}
        response = requests.request(method, f"{API}{path}", headers=self._headers(repo),
                                    json=payload, timeout=_TIMEOUT)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} → {response.status_code}: {response.text[:400]}")
        return response.json() if response.text else {}

    # --- чтение состояния ---

    def default_branch(self, repo: str) -> str:
        return self._get(repo, f"/repos/{repo}")["default_branch"]

    def branch_sha(self, repo: str, branch: str) -> str:
        data = self._get(repo, f"/repos/{repo}/commits/{branch}")
        return data["sha"]

    def open_pulls(self, repo: str) -> list[PullFacts]:
        """Список открытых PR — по одному запросу на PR за подробностями.

        Список из `/pulls` не несёт ни `mergeable`, ни объёма правки: GitHub
        считает мержабельность лениво и отдаёт её только в карточке одного PR.
        Поэтому список открытых — дешёвый запрос, а факты — по одному на PR.
        """
        listing = self._get(repo, f"/repos/{repo}/pulls", state="open", per_page=100)
        return [self.pull_facts(repo, item["number"]) for item in listing]

    def pull_facts(self, repo: str, number: int) -> PullFacts:
        pull = self._pull_with_mergeability(repo, number)
        files = [f["filename"] for f in
                 self._get(repo, f"/repos/{repo}/pulls/{number}/files", per_page=100)]
        approved, decision, review_notes = self._review_decision(repo, number)
        head_sha = pull["head"]["sha"]
        head_time = self._commit_time(repo, head_sha)
        labels = [label["name"] for label in pull.get("labels", [])]
        notes = review_notes + self._comment_notes(repo, number)
        verdict, reason = review_module.verdict(head_sha, head_time, decision, labels, notes)
        return PullFacts(
            number=number,
            title=pull.get("title", ""),
            author=(pull.get("user") or {}).get("login", ""),
            base=pull["base"]["ref"],
            head_ref=pull["head"]["ref"],
            head_sha=head_sha,
            draft=bool(pull.get("draft")),
            approved=approved,
            review_decision=decision,
            labels=labels,
            mergeable=pull.get("mergeable"),
            mergeable_state=pull.get("mergeable_state", "unknown"),
            checks_state=self.checks_state(repo, head_sha),
            files=files,
            additions=pull.get("additions", 0),
            deletions=pull.get("deletions", 0),
            updated_at=pull.get("updated_at", ""),
            body=pull.get("body") or "",
            head_committed_at=head_time,
            review_verdict=verdict,
            review_reason=reason,
        )

    def _commit_time(self, repo: str, sha: str) -> str:
        """Время текущего коммита ветки — точка отсчёта свежести вердикта ревью."""
        data = self._get(repo, f"/repos/{repo}/commits/{sha}")
        commit = data.get("commit", {})
        return (commit.get("committer") or {}).get("date", "")

    def _comment_notes(self, repo: str, number: int) -> list[dict]:
        """Комментарии PR в виде, достаточном для вердикта.

        Тело режется: вердикт живёт в первых строках, а целиком комментарии
        PR-Agent весят десятки килобайт и в payload активности им делать нечего.
        """
        raw = self._get(repo, f"/repos/{repo}/issues/{number}/comments", per_page=100)
        return [{
            "author": (item.get("user") or {}).get("login", ""),
            "created_at": item.get("created_at", ""),
            "body": (item.get("body") or "")[:2000],
            "kind": "comment",
        } for item in raw]

    def _pull_with_mergeability(self, repo: str, number: int) -> dict:
        """Карточка PR, дочитанная до определённой мержабельности.

        `mergeable: null` — не «конфликта нет» и не «конфликт есть», а «расчёт
        запущен, ответа пока нет». Решать по нему нельзя: на живом релизе
        `release-2026-08-24.2` из-за этого выпали ЧЕТЫРЕ PR сразу — предыдущий
        мерж в базу обнулил расчёт у всех открытых, и снимок состояния застал
        их всех в неизвестности.

        Ждём в активности, а не в воркфлоу: это секунды, и дробить их на шаги
        истории Temporal незачем.
        """
        for attempt in range(_MERGEABLE_TRIES):
            pull = self._get(repo, f"/repos/{repo}/pulls/{number}")
            if pull.get("mergeable") is not None or pull.get("state") != "open":
                return pull
            if attempt + 1 < _MERGEABLE_TRIES:
                time.sleep(_MERGEABLE_PAUSE)
        _log.warning("мержабельность %s#%s не досчиталась за %s попыток",
                     repo, number, _MERGEABLE_TRIES)
        return pull

    def _review_decision(self, repo: str, number: int) -> tuple[bool, str, list[dict]]:
        """Одобрен ли PR — по последнему отзыву каждого ревьюера.

        Без GraphQL: REST не отдаёт `reviewDecision`, зато отдаёт список
        отзывов. Правило то же, что у GitHub, — считается ПОСЛЕДНИЙ отзыв
        человека, иначе снятое замечание навсегда блокировало бы PR.
        """
        reviews = self._get(repo, f"/repos/{repo}/pulls/{number}/reviews", per_page=100)
        latest: dict[str, str] = {}
        notes: list[dict] = []
        for review in reviews:
            state = review.get("state", "")
            if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                latest[(review.get("user") or {}).get("login", "")] = state
            if state == "APPROVED":
                # Отдельным видом: свежесть APPROVED считается по времени
                # ОТПРАВКИ ревью, а не по комментариям вокруг него.
                notes.append({
                    "author": (review.get("user") or {}).get("login", ""),
                    "created_at": review.get("submitted_at", ""),
                    "body": "",
                    "kind": "review-approved",
                })
        if any(state == "CHANGES_REQUESTED" for state in latest.values()):
            return False, "CHANGES_REQUESTED", notes
        if any(state == "APPROVED" for state in latest.values()):
            return True, "APPROVED", notes
        return False, ("REVIEW_REQUIRED" if not latest else "COMMENTED"), notes

    def checks_state(self, repo: str, sha: str) -> str:
        """Сводное состояние проверок коммита: success | failure | pending | none.

        Смотрим и check-runs (GitHub Actions), и статусы (внешние сервисы): в
        одном репозитории живут оба механизма, и учёт одного даёт зелёный свет
        по красному соседу.
        """
        state = "none"
        runs = self._get(repo, f"/repos/{repo}/commits/{sha}/check-runs", per_page=100)
        for run in runs.get("check_runs", []):
            if run.get("status") != "completed":
                state = "pending"
                continue
            conclusion = run.get("conclusion")
            if conclusion in ("failure", "timed_out", "cancelled", "action_required"):
                return "failure"
            if conclusion in ("success", "neutral", "skipped") and state == "none":
                state = "success"
        combined = self._get(repo, f"/repos/{repo}/commits/{sha}/status")
        combined_state = combined.get("state", "")
        if combined_state == "failure":
            return "failure"
        if combined_state == "pending" and combined.get("total_count", 0):
            return "pending"
        if combined_state == "success" and state == "none":
            state = "success"
        return state

    def get_file(self, repo: str, path: str, ref: str) -> str | None:
        response = requests.get(f"{API}/repos/{repo}/contents/{path}",
                                headers=self._headers(repo), params={"ref": ref},
                                timeout=_TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return base64.b64decode(data["content"]).decode("utf-8")

    def existing_tags(self, repo: str) -> list[str]:
        releases = self._get(repo, f"/repos/{repo}/releases", per_page=100)
        tags = [item["tag_name"] for item in releases]
        tags += [item["name"] for item in self._get(repo, f"/repos/{repo}/tags", per_page=100)]
        return tags

    # --- мутации ---

    def merge_pull(self, repo: str, number: int, method: str = "squash") -> str:
        data = self._write("PUT", repo, f"/repos/{repo}/pulls/{number}/merge",
                           {"merge_method": method})
        sha = data.get("sha", "")
        if not sha and not self._dry_run:
            raise RuntimeError(f"мерж #{number} не вернул sha: {data}")
        return sha

    def create_release(self, repo: str, tag: str, name: str, body: str,
                       target: str, draft: bool = True) -> tuple[int, str]:
        data = self._write("POST", repo, f"/repos/{repo}/releases", {
            "tag_name": tag, "name": name, "body": body,
            "target_commitish": target, "draft": draft, "prerelease": False,
        })
        return data.get("id", 0), data.get("html_url", "")

    def update_release(self, repo: str, release_id: int, body: str, draft: bool = False) -> str:
        data = self._write("PATCH", repo, f"/repos/{repo}/releases/{release_id}",
                           {"body": body, "draft": draft})
        return data.get("html_url", "")

    def comment(self, repo: str, number: int, body: str) -> None:
        self._write("POST", repo, f"/repos/{repo}/issues/{number}/comments", {"body": body})

    def revert_merge(self, repo: str, merge_sha: str, branch: str) -> str:
        """Откат влитого изменения — `git revert` поверх базовой ветки.

        Через git, а не через REST: у GitHub нет API отката коммита, а `revert`
        мержа требует указания родителя (`-m 1`). Пуш идёт тем же токеном, что
        и остальные операции, и в историю попадает обычный коммит — откат
        обязан быть виден в репозитории, а не только в логах агента.
        """
        if self._dry_run:
            _log.info("[DRY_RUN] revert %s in %s", merge_sha, repo)
            return ""
        token = self._token_for(repo)
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ,
                   "GIT_TERMINAL_PROMPT": "0",
                   "GIT_AUTHOR_NAME": "poh-delivery-agent",
                   "GIT_AUTHOR_EMAIL": "delivery-agent@po-helper.local",
                   "GIT_COMMITTER_NAME": "poh-delivery-agent",
                   "GIT_COMMITTER_EMAIL": "delivery-agent@po-helper.local"}
            def run(command: str) -> str:
                result = subprocess.run(shlex.split(command), cwd=tmp, env=env,
                                        capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    raise RuntimeError(f"{command} → {result.returncode}: "
                                       f"{(result.stdout + result.stderr)[-500:]}")
                return result.stdout.strip()

            run(f"git clone --quiet --branch {branch} {url} .")
            # `-m 1` — вернуть состояние базовой ветки: у мерж-коммита первый
            # родитель это база, второй — влитая ветка. Squash-мерж родителя не
            # имеет вовсе, там revert обычный, поэтому пробуем оба.
            try:
                run(f"git revert --no-edit -m 1 {merge_sha}")
            except RuntimeError:
                run(f"git revert --no-edit {merge_sha}")
            run(f"git push origin {branch}")
            return run("git rev-parse HEAD")
