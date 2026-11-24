"""Mission Control: the firm, its bots, and what each one is doing now.

Three parts, kept apart on purpose:

* ``firm``     -- the static model: offices, practices, the bot roster
                  (``firm/firm.yaml``) and the repo ground truth
                  (``firm/inventory.yaml``).
* ``state``    -- the live part: events bots post, and each bot's status
                  derived from how recently it reported.
* ``nextmove`` -- deterministic recommendations, each one carrying the
                  evidence it was derived from. No model decides what the
                  managing partner should look at next.
"""

from opencloudops.fleet.firm import Bot, Firm, load_firm, load_inventory
from opencloudops.fleet.nextmove import next_moves
from opencloudops.fleet.state import EVENT_KINDS, FleetState

__all__ = ["Bot", "Firm", "load_firm", "load_inventory", "FleetState", "EVENT_KINDS", "next_moves"]
