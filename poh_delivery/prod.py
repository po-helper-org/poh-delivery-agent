"""Прод-контур: выкатка влитого состояния и проверка живого сервиса.

Прод здесь — контейнер на том же хосте, что и воркер, поднятый из состояния
базовой ветки. Это осознанно скромная модель: смысл Delivery-Agent не в том,
чтобы уметь пять способов деплоя, а в том, чтобы после мержа существовало
место, где изменение РАБОТАЕТ, и проверка, которая это подтверждает или
опровергает. Замена модели — замена реализации порта, воркфлоу не меняется.

Почему общий именованный том, а не bind-mount рабочего каталога: воркер сам
живёт в контейнере, и его пути на хосте не существуют — та же ловушка, что у
агента разработки (`shared/develop.py`). Том виден обоим.
"""

import json
import logging
import os
import shlex
import socket
import subprocess
import time

import requests
from temporalio import activity

from poh_delivery.model import CheckResult, CheckSpec, DeployResult, ObservationResult

_log = logging.getLogger(__name__)

WORKSPACE_VOLUME = os.environ.get("DELIVERY_WORKSPACE_VOLUME", "poh-dev-workspace")
WORKSPACE_MOUNT = os.environ.get("DELIVERY_WORKSPACE_MOUNT", "/workspaces")
CONTAINER = os.environ.get("DELIVERY_PROD_CONTAINER", "poh-delivery-prod")
RUNTIME_IMAGE = os.environ.get("DELIVERY_RUNTIME_IMAGE", "node:22-slim")
SHA_LABEL = "poh.delivery.sha"
READY_TIMEOUT = int(os.environ.get("DELIVERY_READY_TIMEOUT", "90"))
HTTP_TIMEOUT = int(os.environ.get("DELIVERY_HTTP_TIMEOUT", "15"))


def _run(command: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _own_network() -> str:
    """Сеть, в которой стоит сам воркер, — в неё же ставится прод-контейнер.

    Иначе проверка не дотянется до сервиса: публиковать порт на хост ради
    собственной же проверки значит открыть прод наружу без нужды.
    """
    configured = os.environ.get("DELIVERY_PROD_NETWORK", "").strip()
    if configured:
        return configured
    own = _run(["docker", "inspect", socket.gethostname(), "--format",
                "{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}"])
    networks = (own.stdout or "").split()
    return networks[0] if networks else "bridge"


class DockerProd:
    """Прод-контур как контейнер, поднятый из клона репозитория на общем томе."""

    def __init__(self, token_provider, dry_run: bool = False):
        self._token_for = token_provider
        self._dry_run = dry_run

    # --- выкатка ---

    def _release_dir(self, repo: str, sha: str) -> str:
        slug = repo.replace("/", "__")
        return f"{WORKSPACE_MOUNT}/delivery/{slug}/{sha}"

    def _materialize(self, repo: str, sha: str) -> str:
        """Разложить состояние репозитория на нужном SHA в каталог тома.

        Каталог именуется SHA и НЕ переиспользуется: откат — это возврат к уже
        разложенному прежнему каталогу, а не повторный клон, который в момент
        аварии может не собраться.
        """
        target = self._release_dir(repo, sha)
        if os.path.isdir(os.path.join(target, ".git")):
            return target
        os.makedirs(target, exist_ok=True)
        token = self._token_for(repo)
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
        steps = [
            ["git", "init", "--quiet", target],
            ["git", "-C", target, "remote", "add", "origin", url],
            ["git", "-C", target, "fetch", "--quiet", "--depth", "1", "origin", sha],
            ["git", "-C", target, "checkout", "--quiet", "FETCH_HEAD"],
        ]
        for step in steps:
            result = _run(step)
            if result.returncode != 0:
                raise RuntimeError(f"{' '.join(step[:3])} → {result.returncode}: "
                                   f"{(result.stdout + result.stderr)[-400:]}")
        return target

    def deploy(self, repo: str, sha: str, service: dict) -> DeployResult:
        if self._dry_run:
            _log.info("[DRY_RUN] deploy %s@%s", repo, sha)
            return DeployResult(ok=True, sha=sha, detail="[DRY_RUN]")

        workdir = self._materialize(repo, sha)
        port = int(service.get("port", 8080))
        start = service.get("start", "")
        if not start:
            return DeployResult(ok=False, sha=sha,
                                detail="в checks.json не задан service.start — нечем поднимать сервис")
        image = service.get("image", RUNTIME_IMAGE)

        _run(["docker", "rm", "-f", CONTAINER])
        command = [
            "docker", "run", "-d", "--name", CONTAINER,
            "--label", f"{SHA_LABEL}={sha}",
            "--network", _own_network(),
            "-v", f"{WORKSPACE_VOLUME}:{WORKSPACE_MOUNT}",
            "-w", workdir,
            "-e", f"PORT={port}",
            "--restart", "unless-stopped",
            image, "sh", "-c", start,
        ]
        result = _run(command)
        if result.returncode != 0:
            return DeployResult(ok=False, sha=sha,
                                detail=f"docker run: {(result.stdout + result.stderr)[-400:]}")

        ready, detail = self._wait_ready(port, service)
        if not ready:
            return DeployResult(ok=False, sha=sha, detail=detail)
        return DeployResult(ok=True, sha=sha, url=f"http://{CONTAINER}:{port}",
                            detail="сервис поднялся")

    def _wait_ready(self, port: int, service: dict) -> tuple[bool, str]:
        health = service.get("health_path", "/")
        deadline = time.time() + READY_TIMEOUT
        last = ""
        while time.time() < deadline:
            try:
                response = requests.get(f"http://{CONTAINER}:{port}{health}", timeout=5)
                # Любой ответ означает, что процесс слушает порт: 404 на корне
                # у сервиса без корневого маршрута — норма, а не незапуск.
                if response.status_code < 500:
                    return True, f"ответ {response.status_code} на {health}"
                last = f"HTTP {response.status_code}"
            except Exception as error:  # соединение ещё не поднялось
                last = str(error)[:200]
            time.sleep(2)
        logs = _run(["docker", "logs", "--tail", "40", CONTAINER])
        return False, (f"сервис не ответил за {READY_TIMEOUT}s ({last}); "
                       f"логи: {(logs.stdout + logs.stderr)[-500:]}")

    def current_sha(self) -> str:
        result = _run(["docker", "inspect", CONTAINER, "--format",
                       f"{{{{index .Config.Labels \"{SHA_LABEL}\"}}}}"])
        return (result.stdout or "").strip() if result.returncode == 0 else ""

    # --- проверка ---

    def verify(self, checks: list[CheckSpec], service: dict) -> list[CheckResult]:
        port = int(service.get("port", 8080))
        base = f"http://{CONTAINER}:{port}"
        results: list[CheckResult] = []
        for check in checks:
            results.append(self._one_check(base, check))
        return results

    def _one_check(self, base: str, check: CheckSpec) -> CheckResult:
        try:
            response = requests.request(
                check.method.upper(), f"{base}{check.path}",
                json=check.body if check.body is not None else None,
                timeout=HTTP_TIMEOUT,
            )
        except Exception as error:
            return CheckResult(check.name, False, f"запрос не прошёл: {str(error)[:200]}")

        if response.status_code != check.expect_status:
            return CheckResult(check.name, False,
                               f"ожидался HTTP {check.expect_status}, пришёл {response.status_code}: "
                               f"{response.text[:200]}")
        if check.expect_contains and check.expect_contains not in response.text:
            return CheckResult(check.name, False,
                               f"в ответе нет `{check.expect_contains}`: {response.text[:200]}")
        if check.expect_json:
            try:
                payload = response.json()
            except Exception:
                return CheckResult(check.name, False, f"ответ не JSON: {response.text[:200]}")
            for key, expected in check.expect_json.items():
                actual = payload.get(key) if isinstance(payload, dict) else None
                if actual != expected:
                    return CheckResult(check.name, False,
                                       f"поле `{key}`: ожидалось {expected!r}, пришло {actual!r}")
        return CheckResult(check.name, True, f"HTTP {response.status_code}")

    # --- наблюдение ---

    def observe(self, duration: int, service: dict) -> ObservationResult:
        """Наблюдение за контейнером после выкатки.

        Проверяет, что контейнер жив весь период наблюдения, не перезапускается
        и сохраняет стабильность PID.
        """
        if self._dry_run:
            _log.info("[DRY_RUN] observe %ds", duration)
            return ObservationResult(duration=duration, alive=True, restarts=0, detail="[DRY_RUN]")

        if duration <= 0:
            return ObservationResult(duration=0, alive=True, restarts=0, detail="наблюдение отключено (duration=0)")

        # Получаем начальный PID контейнера
        initial_pid = self._get_container_pid()
        if initial_pid is None:
            return ObservationResult(duration=0, alive=False, restarts=0,
                                   detail="контейнер не найден или не запущен")

        start_time = time.time()
        end_time = start_time + duration
        check_interval = 10  # проверять каждые 10 секунд

        last_status = "running"
        last_restarts = 0

        while time.time() < end_time:
            time.sleep(check_interval)
            activity.heartbeat()  # Отправляем heartbeat на каждой итерации
            
            # Проверяем статус контейнера
            status = self._get_container_status()
            if status != "running":
                elapsed = int(time.time() - start_time)
                if status is None:
                    return ObservationResult(duration=elapsed, alive=False, restarts=last_restarts,
                                           detail=f"состояние контейнера неизвестно на секунде {elapsed}")
                return ObservationResult(duration=elapsed, alive=False, restarts=last_restarts,
                                       detail=f"контейнер перешёл в статус '{status}' на секунде {elapsed}")
            
            # Проверяем количество перезапусков
            restarts = self._get_container_restart_count()
            if restarts is None:
                _log.warning("не удалось получить restart count через docker inspect")
                restarts = 0  # fallback, если docker не отдаёт restart count
            
            if restarts > last_restarts:
                elapsed = int(time.time() - start_time)
                return ObservationResult(duration=elapsed, alive=False, restarts=restarts,
                                       detail=f"контейнер перезапустился на секунде {elapsed} (всего перезапусков: {restarts})")
            last_restarts = restarts
            
            # Проверяем стабильность PID
            current_pid = self._get_container_pid()
            if current_pid is None:
                elapsed = int(time.time() - start_time)
                return ObservationResult(duration=elapsed, alive=False, restarts=last_restarts,
                                       detail=f"PID контейнера недоступен на секунде {elapsed}")
            
            if current_pid != initial_pid:
                elapsed = int(time.time() - start_time)
                return ObservationResult(duration=elapsed, alive=False, restarts=last_restarts,
                                       detail=f"PID изменился с {initial_pid} на {current_pid} на секунде {elapsed}")
            
            last_status = status

        # Успешное прохождение окна
        final_duration = int(time.time() - start_time)
        return ObservationResult(duration=final_duration, alive=True, restarts=last_restarts,
                               detail=f"контейнер прожил {final_duration}с без инцидентов")

    def _get_container_status(self) -> str | None:
        """Получить статус контейнера через docker inspect."""
        result = _run(["docker", "inspect", CONTAINER, "--format", "{{.State.Status}}"])
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip()

    def _get_container_restart_count(self) -> int | None:
        """Получить количество перезапусков контейнера через docker inspect."""
        result = _run(["docker", "inspect", CONTAINER, "--format", "{{.State.RestartCount}}"])
        if result.returncode != 0:
            return None
        try:
            return int((result.stdout or "").strip())
        except ValueError:
            return None

    def _get_container_pid(self) -> int | None:
        """Получить PID главного процесса контейнера через docker inspect."""
        result = _run(["docker", "inspect", CONTAINER, "--format", "{{.State.Pid}}"])
        if result.returncode != 0:
            return None
        try:
            return int((result.stdout or "").strip())
        except ValueError:
            return None


def parse_checks(raw: str) -> tuple[dict, list[CheckSpec]]:
    """Разбор `.delivery/checks.json` целевого репозитория.

    Формат намеренно простой и лежит в САМОМ репозитории: что считать
    «работает как в БФТ», описывает команда репозитория, а не агент. Агент это
    исполняет и не имеет права додумывать.
    """
    data = json.loads(raw)
    service = data.get("service", {})
    checks = []
    for item in data.get("checks", []):
        checks.append(CheckSpec(
            name=item["name"],
            path=item.get("path", "/"),
            method=item.get("method", "GET"),
            body=item.get("body"),
            expect_status=int(item.get("expect_status", 200)),
            expect_json=item.get("expect_json", {}) or {},
            expect_contains=item.get("expect_contains", ""),
            source=item.get("source", ""),
        ))
    return service, checks
