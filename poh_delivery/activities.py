"""Активности релиза — весь ввод-вывод Delivery-Agent.

Активности синхронные: они делают блокирующие HTTP- и docker-вызовы, и воркер
Harness гоняет такие в пуле потоков, не занимая событийный цикл воркфлоу (тот
же приём, что у активностей Issue-Agent).

Каждая активность — один внешний эффект. Дробность не эстетика: история
Temporal и есть журнал релиза, и «влил PR» обязано быть отдельным событием от
«выкатил» и «проверил», иначе разбор аварии не отличит одно от другого.
"""

import logging

from temporalio import activity

from poh_delivery import memory, ports, prod as prod_module
from poh_delivery.model import (
    CheckResult,
    CheckSpec,
    ChecksBundle,
    DeployResult,
    ObservationResult,
    PullFacts,
    ReleaseRef,
    RepoState,
)

_log = logging.getLogger(__name__)

CHECKS_PATH = ".delivery/checks.json"


@activity.defn(name="delivery_collect_state")
def collect_state(repo: str) -> RepoState:
    api = ports.github()
    branch = api.default_branch(repo)
    return RepoState(repo=repo, default_branch=branch,
                     base_sha=api.branch_sha(repo, branch),
                     pulls=api.open_pulls(repo))


@activity.defn(name="delivery_pull_facts")
def pull_facts(repo: str, number: int) -> PullFacts:
    return ports.github().pull_facts(repo, number)


@activity.defn(name="delivery_read_checks")
def read_checks(repo: str, ref: str) -> ChecksBundle:
    """Проверки берутся из репозитория-цели, а не из агента.

    Нет файла — не отказ: релиз пойдёт, но в плане будет честно написано, что
    подтверждать поведение нечем, кроме факта запуска сервиса.
    """
    raw = ports.github().get_file(repo, CHECKS_PATH, ref)
    if not raw:
        return ChecksBundle(source="", service={}, checks=[])
    service, checks = prod_module.parse_checks(raw)
    return ChecksBundle(source=f"{CHECKS_PATH}@{ref[:12]}", service=service, checks=checks)


@activity.defn(name="delivery_existing_tags")
def existing_tags(repo: str) -> list[str]:
    return ports.github().existing_tags(repo)


@activity.defn(name="delivery_create_release")
def create_release(repo: str, tag: str, title: str, body: str, target: str) -> ReleaseRef:
    """Релиз создаётся ЧЕРНОВИКОМ до первого мержа.

    Ссылка нужна раньше выкатки — её агент публикует в каждом входящем PR, — а
    опубликованный релиз до отгрузки означал бы «уже отгружено». Черновик
    снимает противоречие: документ существует, факта поставки ещё нет.
    """
    release_id, url = ports.github().create_release(repo, tag, title, body, target, draft=True)
    return ReleaseRef(release_id=release_id, url=url, tag=tag)


@activity.defn(name="delivery_update_release")
def update_release(repo: str, release_id: int, body: str, publish: bool) -> str:
    return ports.github().update_release(repo, release_id, body, draft=not publish)


@activity.defn(name="delivery_comment")
def comment(repo: str, number: int, body: str) -> None:
    ports.github().comment(repo, number, body)


@activity.defn(name="delivery_merge")
def merge(repo: str, number: int, method: str) -> str:
    sha = ports.github().merge_pull(repo, number, method)
    _log.info("влит #%s → %s", number, sha[:12])
    return sha


@activity.defn(name="delivery_deploy")
def deploy(repo: str, sha: str, service: dict) -> DeployResult:
    return ports.prod().deploy(repo, sha, service)


@activity.defn(name="delivery_verify")
def verify(checks: list[CheckSpec], service: dict) -> list[CheckResult]:
    if not checks:
        return []
    return ports.prod().verify(checks, service)


@activity.defn(name="delivery_observe")
def observe(duration: int, service: dict) -> ObservationResult:
    """Наблюдение за контейнером после выкатки.

    Проверяет, что контейнер жив весь период наблюдения, не перезапускается
    и сохраняет стабильность PID.
    """
    return ports.prod().observe(duration, service)


@activity.defn(name="delivery_revert")
def revert(repo: str, merge_sha: str, branch: str) -> str:
    return ports.github().revert_merge(repo, merge_sha, branch)


@activity.defn(name="delivery_memory_rules")
def memory_rules(repo: str) -> dict:
    """Правила и накопленный опыт организации для роли поставки.

    Отдельной активностью, а не чтением внутри воркфлоу: воркфлоу не ходит в
    сеть и не читает окружение — иначе воспроизведение истории брало бы другое
    значение и роняло идущий прогон недетерминизмом.

    Слой не подключён либо недоступен — пустой блок. План релиза при этом
    собирается ровно как раньше.
    """
    got = memory.rules(memory.DELIVERY, repo=repo)
    return {"text": got.text, "ids": got.ids}


@activity.defn(name="delivery_capture_episode")
def capture_episode(episode: dict) -> bool:
    """Запись об итерации поставки — слою саморефлексии.

    Пишется то, что уже известно коду: что отгружали, чем кончилось, какие
    правила при этом действовали. Оценивать здесь нечего — факты о том, пережил
    ли релиз контакт с продом, созреют позже, и их соберёт отложенный проход.
    """
    return memory.put_episode(episode)


@activity.defn(name="delivery_prod_sha")
def prod_sha() -> str:
    return ports.prod().current_sha()


ALL = [
    collect_state,
    pull_facts,
    read_checks,
    existing_tags,
    create_release,
    update_release,
    comment,
    merge,
    deploy,
    verify,
    observe,
    revert,
    prod_sha,
    memory_rules,
    capture_episode,
]
