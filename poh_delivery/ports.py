"""Порты: чем воркфлоу разговаривает с GitHub и с прод-контуром.

Порт, а не прямой вызов клиента Harness, — потому что Delivery-Agent живёт в
своём репозитории и обязан собираться и тестироваться без него. Harness
подставляет реализации на старте воркера (`configure`), тесты — свои заглушки.

Второй эффект тот же, что в разборе адаптации под GitLab: провайдер трекера
меняется заменой реализации порта, а не правкой воркфлоу.
"""

from typing import Protocol

from poh_delivery.model import CheckResult, CheckSpec, DeployResult, PullFacts


class GitHubPort(Protocol):
    def default_branch(self, repo: str) -> str: ...
    def branch_sha(self, repo: str, branch: str) -> str: ...
    def open_pulls(self, repo: str) -> list[PullFacts]: ...
    def pull_facts(self, repo: str, number: int) -> PullFacts: ...
    def merge_pull(self, repo: str, number: int, method: str) -> str: ...
    def existing_tags(self, repo: str) -> list[str]: ...
    def create_release(self, repo: str, tag: str, name: str, body: str,
                       target: str, draft: bool) -> tuple[int, str]: ...
    def update_release(self, repo: str, release_id: int, body: str,
                       draft: bool) -> str: ...
    def comment(self, repo: str, number: int, body: str) -> None: ...
    def get_file(self, repo: str, path: str, ref: str) -> str | None: ...
    def revert_merge(self, repo: str, merge_sha: str, branch: str) -> str: ...


class ProdPort(Protocol):
    """Прод-контур: куда уезжает влитое и чем проверяется, что оно живо."""

    def deploy(self, repo: str, sha: str, service: dict) -> DeployResult: ...
    def verify(self, checks: list[CheckSpec], service: dict) -> list[CheckResult]: ...
    def current_sha(self) -> str: ...


_github: GitHubPort | None = None
_prod: ProdPort | None = None


def configure(github: GitHubPort | None = None, prod: ProdPort | None = None) -> None:
    """Подставить реализации портов. Зовётся один раз на старте воркера."""
    global _github, _prod
    if github is not None:
        _github = github
    if prod is not None:
        _prod = prod


def github() -> GitHubPort:
    if _github is None:
        raise RuntimeError("GitHub-порт не сконфигурирован: вызови poh_delivery.ports.configure()")
    return _github


def prod() -> ProdPort:
    if _prod is None:
        raise RuntimeError("Прод-порт не сконфигурирован: вызови poh_delivery.ports.configure()")
    return _prod
