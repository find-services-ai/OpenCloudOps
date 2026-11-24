"""HTTP surface for Mission Control.

Reads are open on purpose: put the service behind an identity-aware proxy
(Cloudflare Access, IAP) rather than inventing a login here. Writes -- bots
posting events -- need a bearer token, and when no token is configured the
ingest endpoint refuses everything. It fails closed; it does not fall open.
"""

from __future__ import annotations

import hmac
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from opencloudops.fleet.firm import load_firm, load_inventory
from opencloudops.fleet.nextmove import next_moves
from opencloudops.fleet.state import EVENT_KINDS, FleetState

router = APIRouter()
_CONSOLE = Path(__file__).with_name("console.html")


def _path(var: str, default: str) -> Path:
    return Path(os.environ.get(var, default))


@lru_cache(maxsize=1)
def get_state() -> FleetState:
    log = os.environ.get("OCC_FLEET_LOG", "")
    return FleetState(load_firm(_path("OCC_FIRM", "firm/firm.yaml")), Path(log) if log else None)


def _inventory():
    p = _path("OCC_INVENTORY", "firm/inventory.yaml")
    return load_inventory(p) if p.exists() else None


class EventIn(BaseModel):
    kind: str = Field(description=f"One of: {', '.join(EVENT_KINDS)}")
    summary: str = ""
    agent: str = ""
    repo: str = ""
    ref: str = ""
    url: str = ""
    ts: str | None = None
    synthetic: bool = False


def _check_token(authorization: str | None) -> None:
    expected = os.environ.get("OCC_INGEST_TOKEN", "")
    if not expected:
        raise HTTPException(503, "ingest disabled: OCC_INGEST_TOKEN is not set")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given, expected):
        raise HTTPException(401, "bad or missing bearer token")


@router.get("/console", response_class=HTMLResponse, include_in_schema=False)
def console() -> str:
    return _CONSOLE.read_text()


@router.get("/fleet")
def fleet() -> dict[str, Any]:
    state = get_state()
    firm = state.firm
    return {
        "firm": firm.firm.model_dump(),
        "offices": [o.model_dump() for o in firm.offices],
        "practices": [p.model_dump() for p in firm.practices],
        "bots": state.bot_status(),
        "summary": state.summary(),
    }


@router.get("/fleet/events")
def list_events(limit: int = 50) -> list[dict[str, Any]]:
    return get_state().events(min(max(limit, 1), 500))


@router.post("/fleet/events", status_code=202)
def post_event(body: EventIn, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _check_token(authorization)
    try:
        return get_state().record(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/fleet/next-moves")
def moves() -> list[dict[str, Any]]:
    return next_moves(get_state(), _inventory())


@router.get("/fleet/inventory")
def inventory() -> dict[str, Any]:
    inv = _inventory()
    if inv is None:
        raise HTTPException(404, "no inventory file")
    return inv.model_dump()
