"""IaC emitters: one resolved plan, many target languages.

Emitters are deliberately dumb. They receive a ``ResolvedPlan`` in which every
node is already grounded in an approved pattern, already named by the platform
contract, and already tagged -- so an emitter never decides a name, never picks
a resource, and never relaxes a control. It renders.

That split is what makes five backends maintainable: a naming change or a new
security control is one edit in the core, not five edits in five templates.
"""

from opencloudops.emitters.base import (  # noqa: F401
    EMITTERS,
    Emitter,
    available,
    get_emitter,
)
from opencloudops.emitters import (  # noqa: F401
    cdk_typescript,
    cloudformation,
    kubernetes,
    pulumi_python,
    terraform,
)

__all__ = ["Emitter", "EMITTERS", "get_emitter", "available"]
