"""Слой саморефлексии в Delivery-Agent: выключатель и попадание в план."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from poh_delivery import memory, render
from poh_delivery.model import ReleasePlan, StepPlan


@pytest.fixture(autouse=True)
def layer_off(monkeypatch):
    monkeypatch.delenv("MEMORY_BASE_URL", raising=False)
    monkeypatch.delenv("MEMORY_BASE_TOKEN", raising=False)


class _H(BaseHTTPRequestHandler):
    calls: list = []

    def log_message(self, *a):
        pass

    def _reply(self, body):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        type(self).calls.append(("GET", self.path))
        self._reply({"text": "\nПравила:\n- пинуй полным SHA", "ids": ["V-001"], "dropped": 0})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        type(self).calls.append(("POST", self.path, json.loads(self.rfile.read(n))))
        self._reply({"run_id": "r", "path": "p"})


@pytest.fixture
def server(monkeypatch):
    _H.calls = []
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("MEMORY_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("MEMORY_BASE_TOKEN", "t")
    yield _H
    srv.shutdown()


def _plan():
    return ReleasePlan(repo="o/r", base_sha="a" * 40, tag="v1", title="Релиз",
                       steps=[StepPlan(order=1, pr_number=7, title="фикс",
                                       risk="низкий", reason="одобрен",
                                       depends_on=[])],
                       checks=[], checks_source="—", skipped=[], delegated=[])


# ───────────────────── выключатель ─────────────────────

def test_layer_is_off_without_url():
    assert memory.enabled() is False


def test_plan_is_byte_identical_without_rules():
    """Слой не подключён — документ релиза не меняется ни на символ."""
    p = _plan()
    assert render.plan_md(p, "kibarik", "run", "wf") == \
           render.plan_md(p, "kibarik", "run", "wf", org_rules="")


def test_disabled_layer_makes_no_call(monkeypatch):
    called = []
    monkeypatch.setattr(memory.urllib.request, "urlopen", lambda *a, **k: called.append(1))
    memory.rules(memory.DELIVERY, "o/r")
    assert called == []


# ───────────────────── включённый слой ─────────────────────

def test_rules_reach_the_release_plan(server):
    got = memory.rules(memory.DELIVERY, repo="o/r")
    body = render.plan_md(_plan(), "kibarik", "run", "wf", org_rules=got.text)
    assert "Накопленный опыт этой организации" in body
    assert "пинуй полным SHA" in body


def test_delivery_role_is_requested(server):
    memory.rules(memory.DELIVERY, repo="o/r")
    assert "agent=delivery" in server.calls[0][1]


def test_episode_carries_outcome_of_the_release(server):
    ok = memory.put_episode({"run_id": "wf-1", "repo": "o/r", "issue": 3,
                             "phase": "delivery", "agent": "delivery",
                             "rules_injected": ["V-001"],
                             "artifacts": {"steps": 2, "steps_ok": 2}})
    assert ok is True
    _, path, body = server.calls[0]
    assert path == "/episodes"
    assert body["phase"] == "delivery"
    assert body["artifacts"]["steps_ok"] == 2


# ───────────────────── деградация ─────────────────────

def test_unreachable_layer_yields_empty_block(monkeypatch):
    monkeypatch.setenv("MEMORY_BASE_URL", "http://127.0.0.1:1")
    assert memory.rules(memory.DELIVERY, "o/r").text == ""


def test_unreachable_layer_does_not_raise_on_write(monkeypatch):
    monkeypatch.setenv("MEMORY_BASE_URL", "http://127.0.0.1:1")
    assert memory.put_episode({"run_id": "r"}) is False


# ───────────────────── регистрация ─────────────────────

def test_activities_are_registered():
    from poh_delivery import activities
    names = [a.__name__ for a in activities.ALL]
    assert "memory_rules" in names and "capture_episode" in names
