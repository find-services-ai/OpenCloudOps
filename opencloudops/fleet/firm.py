"""The firm model: offices, practices, bots, and repo ground truth."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Maturity = Literal["works", "partial", "stub"]
Autonomy = Literal["read-only", "propose-only", "draft-for-approval"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Office(_Strict):
    id: str
    name: str
    city: str
    role: str


class Practice(_Strict):
    id: str
    name: str
    lead: str
    mandate: str


class Bot(_Strict):
    id: str
    name: str
    practice: str
    office: str
    repo: str
    path: str
    maturity: Maturity
    # There is deliberately no "autonomous" value. A bot that may act on the
    # world without a person is not something this console can represent.
    autonomy: Autonomy
    heartbeat_minutes: int = Field(gt=0)
    does: str


class FirmInfo(_Strict):
    name: str
    tagline: str = ""
    managing_partner: str


class Firm(_Strict):
    firm: FirmInfo
    offices: list[Office]
    practices: list[Practice]
    bots: list[Bot]

    @model_validator(mode="after")
    def _references_resolve(self) -> Firm:
        offices = {o.id for o in self.offices}
        practices = {p.id for p in self.practices}
        seen: set[str] = set()
        for bot in self.bots:
            if bot.id in seen:
                raise ValueError(f"duplicate bot id {bot.id!r}")
            seen.add(bot.id)
            if bot.office not in offices:
                raise ValueError(f"bot {bot.id!r} names unknown office {bot.office!r}")
            if bot.practice not in practices:
                raise ValueError(f"bot {bot.id!r} names unknown practice {bot.practice!r}")
        return self

    def bot(self, bot_id: str) -> Bot | None:
        return next((b for b in self.bots if b.id == bot_id), None)


RepoState = Literal["works", "partial", "stub", "broken", "dead"]
BusinessLine = Literal["client-delivery", "platform", "internal-agents", "ventures", "personal", "archive"]


class RepoFact(_Strict):
    repo: str
    line: BusinessLine
    state: RepoState
    evidence: str
    action: str = ""
    # A fact that is a risk today (exposed data, a broken main) is raised as a
    # decision whatever the repo's state.
    urgent: bool = False


class Inventory(_Strict):
    checked: str
    method: str
    repos: list[RepoFact]


def load_firm(path: Path | str) -> Firm:
    return Firm.model_validate(yaml.safe_load(Path(path).read_text()))


def load_inventory(path: Path | str) -> Inventory:
    return Inventory.model_validate(yaml.safe_load(Path(path).read_text()))
