"""Разбор конфигурации проверок и вердикты по ответу живого сервиса."""

import json

import pytest

from poh_delivery import prod
from poh_delivery.model import CheckSpec

RAW = json.dumps({
    "service": {"port": 8080, "start": "node src/server.mjs", "health_path": "/health"},
    "checks": [
        {"name": "quote-base", "method": "POST", "path": "/quote",
         "body": {"items": [{"sku": "a", "price": 1000, "qty": 2}]},
         "expect_json": {"total": 2300}, "source": "README, раздел «Сервис»"},
    ],
})


def test_parse_checks_reads_service_and_checks():
    service, checks = prod.parse_checks(RAW)
    assert service["start"] == "node src/server.mjs"
    assert checks[0].name == "quote-base"
    assert checks[0].expect_json == {"total": 2300}
    assert checks[0].source.startswith("README")


class _Response:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def api(monkeypatch):
    calls = {}

    def fake_request(method, url, **kwargs):
        calls["method"], calls["url"] = method, url
        return calls["response"]

    monkeypatch.setattr(prod.requests, "request", fake_request)
    return calls


def _check(**kwargs) -> CheckSpec:
    base = dict(name="quote-base", path="/quote", method="POST", expect_status=200)
    base.update(kwargs)
    return CheckSpec(**base)


def test_green_check(api):
    api["response"] = _Response(payload={"total": 2300})
    result = prod.DockerProd(lambda repo: "t")._one_check("http://svc:8080",
                                                          _check(expect_json={"total": 2300}))
    assert result.ok


def test_wrong_field_is_red_and_says_what_came(api):
    api["response"] = _Response(payload={"total": 9999})
    result = prod.DockerProd(lambda repo: "t")._one_check("http://svc:8080",
                                                          _check(expect_json={"total": 2300}))
    assert not result.ok
    assert "2300" in result.detail and "9999" in result.detail


def test_wrong_status_is_red(api):
    api["response"] = _Response(status=500, payload={"error": "boom"})
    result = prod.DockerProd(lambda repo: "t")._one_check("http://svc:8080", _check())
    assert not result.ok
    assert "500" in result.detail


def test_dead_service_is_red_not_exception(monkeypatch):
    def boom(method, url, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(prod.requests, "request", boom)
    result = prod.DockerProd(lambda repo: "t")._one_check("http://svc:8080", _check())
    assert not result.ok
    assert "не прошёл" in result.detail


def test_mergeability_is_polled_until_it_settles(monkeypatch):
    """`mergeable: null` перезапрашивается, а не считается ответом.

    Живой релиз `release-2026-08-24.2`: мерж предыдущего шага обнулил расчёт у
    всех открытых PR, и снимок застал четыре штуки в неизвестности — все
    выпали из релиза.
    """
    from poh_delivery import github as github_module

    monkeypatch.setattr(github_module, "_MERGEABLE_PAUSE", 0)
    answers = [{"mergeable": None, "state": "open"},
               {"mergeable": None, "state": "open"},
               {"mergeable": True, "state": "open", "number": 5}]
    calls = {"n": 0}

    def fake_get(self, repo, path, **params):
        calls["n"] += 1
        return answers[min(calls["n"] - 1, len(answers) - 1)]

    monkeypatch.setattr(github_module.GitHubApi, "_get", fake_get)
    api = github_module.GitHubApi(lambda repo: "t")
    pull = api._pull_with_mergeability("o/r", 5)
    assert pull["mergeable"] is True
    assert calls["n"] == 3


def test_closed_pr_stops_the_polling(monkeypatch):
    """Закрытый PR мержабельность не досчитает никогда — ждать его бессмысленно."""
    from poh_delivery import github as github_module

    monkeypatch.setattr(github_module, "_MERGEABLE_PAUSE", 0)
    monkeypatch.setattr(github_module.GitHubApi, "_get",
                        lambda self, repo, path, **params: {"mergeable": None, "state": "closed"})
    api = github_module.GitHubApi(lambda repo: "t")
    assert api._pull_with_mergeability("o/r", 5)["state"] == "closed"
