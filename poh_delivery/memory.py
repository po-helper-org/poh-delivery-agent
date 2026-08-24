"""Клиент слоя саморефлексии — правила организации и запись об итерации.

Слой живёт отдельным сервисом (`po-helper-org/harness-memory-base`) и хранит
две вещи: правила и принципы, накопленные конкретной организацией, и след того,
чем кончались итерации агентов. Перед работой агент берёт выжимку из правил,
после работы оставляет запись.

**Слой опционален целиком.** Пустой `MEMORY_BASE_URL` — ни одного сетевого
вызова, ни одного изменения в постановках. Это умолчание, и оно закреплено
тестом: постановка при выключенном слое обязана быть побайтово равна постановке
без слоя.

**Отказ слоя не роняет прогон.** Недоступность, таймаут, любой код ответа кроме
двухсотых дают пустой результат и предупреждение в лог. Агент работает без
правил. То же решение, что у интеграции с индексом кода: опциональный источник
обязан деградировать, а не отказывать.

Модуль намеренно чистый — только stdlib, ни Temporal, ни GitHub. Тот же приём,
что у `poh_delivery/rules.py`: решение обязано быть воспроизводимым и не тащить
за собой чужие зависимости.

Копия такого же клиента живёт в контуре (`shared/memory.py`). Копия, а не общая
библиотека, намеренно: Delivery-Agent объявляет ровно одну точку связи с
Harness (`integration.py`), и заводить вторую ради ста строк на `urllib`
дороже, чем эти сто строк держать.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

# Роли агентов контура. Совпадают с каталогами правил на стороне слоя.
ISSUE = "issue"
DEVELOP = "develop"
REVIEW = "review"
DELIVERY = "delivery"
ROLES = (ISSUE, DEVELOP, REVIEW, DELIVERY)

DEFAULT_TIMEOUT_SEC = 5.0
"""Короткий по замыслу.

Слой стоит между агентом и его работой. Если он думает дольше нескольких
секунд, дешевле работать без него: правила полезны, но не настолько, чтобы
ради них держать прогон.
"""

WRITE_TIMEOUT_SEC = 10.0
"""Запись идёт после работы агента и никого не задерживает — потолок мягче."""


@dataclass
class Rules:
    """Блок правил под роль агента.

    `text` дописывается в конец постановки. `ids` уезжает в запись об итерации:
    без него невозможно отличить «правило сработало» от «правило не читали», и
    счётчики подтверждения на стороне слоя теряют смысл.
    """
    text: str = ""
    ids: list[str] = field(default_factory=list)
    dropped: int = 0

    def __bool__(self) -> bool:
        return bool(self.text)


def base_url() -> str:
    return os.environ.get("MEMORY_BASE_URL", "").strip().rstrip("/")


def token() -> str:
    return os.environ.get("MEMORY_BASE_TOKEN", "").strip()


def timeout_sec() -> float:
    try:
        return float(os.environ.get("MEMORY_BASE_TIMEOUT_SEC") or DEFAULT_TIMEOUT_SEC)
    except ValueError:
        return DEFAULT_TIMEOUT_SEC


def enabled() -> bool:
    """Слой подключён только при заданном адресе.

    Токен без адреса ничего не включает: адрес — единственный выключатель, и
    его отсутствие обязано означать полное отсутствие слоя.
    """
    return bool(base_url())


def rules(agent: str, repo: str = "", query: str = "",
          max_items: int | None = None) -> Rules:
    """Взять блок правил под роль агента.

    Возвращает пустой `Rules` при выключенном слое, при любой сетевой ошибке и
    при неизвестной роли. Исключений не поднимает никогда: вызывающий — сборка
    постановки, и она не должна уметь сорваться из-за необязательного источника.
    """
    if not enabled():
        return Rules()
    if agent not in ROLES:
        _log.warning("память: неизвестная роль %r, блок не запрашивается", agent)
        return Rules()

    params = {"agent": agent}
    if repo:
        params["repo"] = repo
    if query:
        # Постановка бывает длинной; в запрос едет только начало — этого
        # достаточно для отбора по близости, а длинный URL ломает прокси.
        # Потолок в символах, а не в байтах: кириллица в URL кодируется до
        # шести байт на символ, и 500 символов дают 3000 байт адреса.
        params["query"] = query[:200]
    if max_items:
        params["max_items"] = str(max_items)

    body = _get("/rules", params)
    if body is None:
        return Rules()
    text = body.get("text") or ""
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    return Rules(text=text, ids=[str(i) for i in ids],
                 dropped=int(body.get("dropped") or 0))


def put_episode(episode: dict) -> bool:
    """Отдать запись об итерации. Возвращает успех, исключений не поднимает.

    Запись идемпотентна по `run_id` на стороне слоя: повторная доставка даёт тот
    же файл, а не второй. Это существенно — события контура доставляются дважды.
    """
    if not enabled():
        return False
    if not episode.get("run_id"):
        _log.warning("память: запись без run_id не отправлена")
        return False
    return _post("/episodes", episode) is not None


def health() -> dict | None:
    """Состояние слоя. Без токена — точка открыта намеренно."""
    if not enabled():
        return None
    return _get("/health", {}, with_token=False)


# ───────────────────────────── транспорт ─────────────────────────────

def _headers(with_token: bool = True) -> dict[str, str]:
    out = {"Accept": "application/json"}
    if with_token and token():
        # Значение обязано быть ASCII: заголовки HTTP кириллицу не принимают,
        # и токен с ней ломается на первом же запросе.
        out["Authorization"] = f"Bearer {token()}"
    return out


def _get(path: str, params: dict, with_token: bool = True) -> dict | None:
    url = base_url() + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers=_headers(with_token), method="GET")
    except ValueError as e:                              # кривой адрес в настройке
        _log.warning("память %s: негодный адрес — %s", path, e)
        return None
    return _send(req, timeout_sec(), path)


def _post(path: str, payload: dict) -> dict | None:
    headers = {**_headers(), "Content-Type": "application/json"}
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base_url() + path, data=data,
                                     headers=headers, method="POST")
    except (ValueError, TypeError) as e:                 # кривой адрес или payload
        _log.warning("память %s: запрос не собран — %s", path, e)
        return None
    return _send(req, WRITE_TIMEOUT_SEC, path)


def _send(req: urllib.request.Request, timeout: float, path: str) -> dict | None:
    """Единственная точка, где ловятся отказы слоя.

    Ловится `Exception`, а не перечень сетевых ошибок: список неполон по
    построению (кривой URL даёт `ValueError`, битый ответ — `JSONDecodeError`),
    а цена пропущенного исключения здесь — сорванный прогон разработки из-за
    необязательного источника.
    """
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status < 200 or resp.status >= 300:
                _log.warning("память %s: код %s", path, resp.status)
                return None
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _log.warning("память %s: HTTP %s", path, e.code)
        return None
    except Exception as e:                               # noqa: BLE001 — см. докстроку
        _log.warning("память %s недоступна: %s", path, e)
        return None
