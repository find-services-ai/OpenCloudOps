"""The SRE plane: derive the setup, then run it.

Two halves, both grounded in the same resolved plan:

* **Setup** (``slo.py``) -- SLOs, alert rules, a dashboard and a runbook,
  derived from the plan rather than written by hand. A service that ships
  without these is a service nobody can be paged for usefully.
* **Operation** (``patterns.py``, ``diagnose.py``, ``remediate.py``) -- classify
  a production signal against a library of known failure shapes, and propose a
  remediation with an honest risk tier attached.

Nothing here executes a remediation. ``remediate.py`` returns a proposal; the
``release-executor`` node applies one, and it is gated at HIGH.
"""

from opencloudops.sre.diagnose import Diagnosis, diagnose  # noqa: F401
from opencloudops.sre.patterns import ERROR_PATTERNS, ErrorPattern  # noqa: F401
from opencloudops.sre.remediate import RemediationProposal, propose  # noqa: F401
from opencloudops.sre.slo import SLOSet, derive_slos, runbook  # noqa: F401

__all__ = [
    "SLOSet",
    "derive_slos",
    "runbook",
    "ERROR_PATTERNS",
    "ErrorPattern",
    "Diagnosis",
    "diagnose",
    "RemediationProposal",
    "propose",
]
