"""Mission Control: firm model, derived status, next moves, ingest auth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opencloudops.fleet import FleetState, load_firm, load_inventory, next_moves
from opencloudops.fleet.firm import Firm

ROOT = Path(__file__).resolve().parents[1]
FIRM = ROOT / "firm" / "firm.yaml"
INVENTORY = ROOT / "firm" / "inventory.yaml"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _state(**kw) -> FleetState:
    return FleetState(load_firm(FIRM), clock=lambda: NOW, **kw)


def _ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def _row(state: FleetState, bot_id: str) -> dict:
    return next(r for r in state.bot_status() if r["id"] == bot_id)


class TestFirmModel:
    def test_shipped_firm_and_inventory_load(self):
        firm = load_firm(FIRM)
        assert firm.bots and firm.practices and firm.offices
        assert load_inventory(INVENTORY).repos

    def test_every_practice_has_a_bot(self):
        firm = load_firm(FIRM)
        assert {p.id for p in firm.practices} == {b.practice for b in firm.bots}

    def test_unknown_office_is_rejected(self):
        raw = load_firm(FIRM).model_dump()
        raw["bots"][0]["office"] = "nowhere"
        with pytest.raises(ValueError, match="unknown office"):
            Firm.model_validate(raw)

    def test_no_autonomous_bot_can_be_declared(self):
        raw = load_firm(FIRM).model_dump()
        raw["bots"][0]["autonomy"] = "autonomous"
        with pytest.raises(ValueError):
            Firm.model_validate(raw)


class TestStatus:
    def test_working_bot_without_events_is_never_reported(self):
        assert _row(_state(), "occ-sre-observer")["status"] == "never-reported"

    def test_stub_stays_stub_even_with_events(self):
        s = _state()
        s.record({"kind": "heartbeat", "agent": "opendelegate", "ts": _ago(1)})
        assert _row(s, "opendelegate")["status"] == "stub"

    @pytest.mark.parametrize(
        "kind,age,expected",
        [
            ("task_started", 5, "working"),
            ("heartbeat", 5, "idle"),
            ("heartbeat", 20, "idle"),      # interval is 15 min; late only past 22.5
            ("heartbeat", 30, "late"),
            ("heartbeat", 50, "silent"),
            ("needs_human", 500, "needs-you"),
        ],
    )
    def test_status_is_derived_from_the_clock(self, kind, age, expected):
        s = _state()
        s.record({"kind": kind, "agent": "occ-sre-observer", "summary": "x", "ts": _ago(age)})
        assert _row(s, "occ-sre-observer")["status"] == expected

    def test_unknown_kind_is_refused(self):
        with pytest.raises(ValueError):
            _state().record({"kind": "all_good_trust_me", "agent": "gos-harness"})

    def test_non_https_url_is_dropped(self):
        ev = _state().record({"kind": "heartbeat", "agent": "gos-harness", "url": "javascript:alert(1)"})
        assert ev["url"] == ""

    def test_events_persist_and_reload(self, tmp_path):
        log = tmp_path / "fleet.jsonl"
        _state(log_path=log).record({"kind": "heartbeat", "agent": "gos-harness", "ts": _ago(1)})
        log.write_text(log.read_text() + '{"torn')  # a half-written last line
        assert _row(_state(log_path=log), "gos-harness")["status"] == "idle"


class TestNextMoves:
    def test_person_blocked_bot_ranks_first(self):
        s = _state()
        s.record({"kind": "heartbeat", "agent": "occ-sre-observer", "ts": _ago(600)})
        s.record({"kind": "needs_human", "agent": "sow-architect", "summary": "Approve SOW draft",
                  "ts": _ago(5)})
        moves = next_moves(s, load_inventory(INVENTORY))
        assert moves[0]["type"] == "decide" and moves[0]["bot"] == "sow-architect"
        assert any(m["type"] == "investigate" and m["bot"] == "occ-sre-observer" for m in moves)

    def test_every_move_carries_evidence(self):
        for m in next_moves(_state(), load_inventory(INVENTORY), limit=50):
            assert m["why"] and m["evidence"]

    def test_urgent_fact_is_a_decision_even_when_repo_mostly_works(self):
        moves = next_moves(_state(), load_inventory(INVENTORY), limit=50)
        assert any("users.json" in m["title"] and m["type"] == "decide" for m in moves)

    def test_broken_repo_becomes_a_decision(self):
        moves = next_moves(_state(), load_inventory(INVENTORY), limit=50)
        assert any("OpenCloudOps" in m["title"] and m["type"] == "decide" for m in moves)


class TestApi:
    @pytest.fixture
    def client(self, monkeypatch):
        from opencloudops.fleet import routes

        monkeypatch.chdir(ROOT)
        monkeypatch.delenv("OCC_FLEET_LOG", raising=False)
        routes.get_state.cache_clear()
        from opencloudops.api import app

        yield TestClient(app)
        routes.get_state.cache_clear()

    def test_console_and_reads(self, client):
        assert "Mission Control" in client.get("/console").text
        body = client.get("/fleet").json()
        assert body["firm"]["name"] == "Axiom Ventures" and body["bots"]
        assert isinstance(client.get("/fleet/next-moves").json(), list)

    def test_ingest_fails_closed_without_a_token(self, client, monkeypatch):
        monkeypatch.delenv("OCC_INGEST_TOKEN", raising=False)
        r = client.post("/fleet/events", json={"kind": "heartbeat", "agent": "gos-harness"})
        assert r.status_code == 503

    def test_ingest_needs_the_right_token(self, client, monkeypatch):
        monkeypatch.setenv("OCC_INGEST_TOKEN", "s3cret")
        bad = client.post("/fleet/events", json={"kind": "heartbeat"},
                          headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
        ok = client.post("/fleet/events", json={"kind": "task_started", "agent": "gos-harness",
                                                "summary": "eval ember"},
                         headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 202
        row = next(b for b in client.get("/fleet").json()["bots"] if b["id"] == "gos-harness")
        assert row["status"] == "working" and row["doing"] == "eval ember"

    def test_harness_event_shape_is_accepted(self, client, monkeypatch):
        """The exact payload gos/events.py builds must ingest unchanged."""
        monkeypatch.setenv("OCC_INGEST_TOKEN", "t")
        event = {"kind": "ci_failed", "summary": "harness tests failed", "repo": "find-services-ai/ethos",
                 "ref": "main", "url": "https://github.com/find-services-ai/ethos/actions",
                 "agent": "gos-harness", "ts": "2026-11-24T11:59:00+00:00"}
        assert client.post("/fleet/events", json=event, headers={"Authorization": "Bearer t"}).status_code == 202
