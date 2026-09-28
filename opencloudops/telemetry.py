"""OpenTelemetry instrumentation for agent work.

Every request through this framework -- a ChatOps command, an API call, a CLI
invocation -- opens one root span, and every agent node that runs inside it
opens a child span. The result is that "why did this plan come out this way"
is answerable from a trace rather than from reading the code: which nodes ran,
in what order, which gate stopped which node, how many repair cycles the
validator drove, and what each one cost in tokens.

Design constraints:

* **OpenTelemetry is optional.** If the SDK is not installed, every call here
  still works and does nothing. A framework that crashes without a collector
  is a framework nobody runs locally.
* **The trace id is the request id.** ``AgentRun.request_id`` is the hex trace
  id when tracing is live, so a ChatOps reply can hand someone a value they
  can paste straight into Tempo/Jaeger/Honeycomb.
* **Attributes are the agent contract**, not free text. The ``ATTR_*``
  constants below are what dashboards and the SRE agent query on, so they are
  named once, here.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

# -- attribute names (the stable contract) ----------------------------------

ATTR_REQUEST_ID = "occ.request.id"
ATTR_NODE = "occ.agent.node"
ATTR_ROLE = "occ.agent.role"
ATTR_MODEL = "occ.agent.model"
ATTR_ZONE = "occ.agent.zone"
ATTR_RISK_GATE = "occ.agent.risk_gate"
ATTR_GATE_RESULT = "occ.gate.result"
ATTR_CYCLE = "occ.agent.cycle"
ATTR_RETRY = "occ.agent.retry"
ATTR_TOKENS = "occ.agent.tokens"
ATTR_TOKEN_BUDGET = "occ.agent.token_budget"
ATTR_DECONTAMINATED = "occ.agent.decontaminated"
ATTR_GRAPH = "occ.graph.name"
ATTR_ENVIRONMENT = "occ.environment"
ATTR_SERVICE = "occ.service"
ATTR_FINDINGS_BLOCK = "occ.guard.blocking"
ATTR_FINDINGS_WARN = "occ.guard.warnings"
ATTR_RISK_TIER = "occ.plan.risk_tier"
ATTR_IAC_LANGUAGE = "occ.iac.language"
ATTR_ANTIPATTERNS = "occ.learner.antipatterns"

SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "opencloudops")

_tracer: Any = None
_configured = False


# -- no-op fallbacks ---------------------------------------------------------


class _NoopSpan:
    """Stands in for a real span when OpenTelemetry is absent. Every method a
    caller uses must exist here, or the fallback path breaks in production and
    not in the tests."""

    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add_event(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get_span_context(self) -> Any:
        return None


def _is_enabled() -> bool:
    return os.environ.get("OCC_OTEL_ENABLED", "1").lower() not in {"0", "false", "no"}


def configure(service_name: str = SERVICE_NAME, *, force: bool = False) -> bool:
    """Set up a tracer provider once. Returns True if tracing is live.

    Exporter selection follows the OTel environment convention:
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` set means export over OTLP/HTTP; unset
    means spans stay in-process (still queryable via ``last_trace_id``), and
    ``OCC_OTEL_CONSOLE=1`` additionally prints them, which is what the demo
    script uses to show the agent graph executing.
    """
    global _tracer, _configured
    if _configured and not force:
        return _tracer is not None
    _configured = True

    if not _is_enabled():
        _tracer = None
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError:
        _tracer = None
        return False

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.environ.get("OCC_VERSION", "0.1.0"),
            "deployment.environment": os.environ.get("OCC_DEPLOY_ENV", "local"),
        }
    )
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except ImportError:
            pass

    if os.environ.get("OCC_OTEL_CONSOLE", "").lower() in {"1", "true", "yes"}:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("opencloudops.agents")
    return True


def tracer() -> Any:
    if not _configured:
        configure()
    return _tracer


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Open a span. Always yields something with ``set_attribute``."""
    t = tracer()
    if t is None:
        yield _NoopSpan()
        return
    with t.start_as_current_span(name) as sp:
        for key, value in attributes.items():
            if value is not None:
                sp.set_attribute(key, value)
        try:
            yield sp
        except Exception as exc:
            sp.record_exception(exc)
            try:
                from opentelemetry.trace import Status, StatusCode

                sp.set_status(Status(StatusCode.ERROR, str(exc)))
            except ImportError:
                pass
            raise


def current_trace_id() -> str | None:
    """Hex trace id of the active span, or None when tracing is off."""
    if tracer() is None:
        return None
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx is None or not ctx.is_valid:
            return None
        return format(ctx.trace_id, "032x")
    except Exception:
        return None


def new_request_id() -> str:
    """A request id to use before any span exists (CLI entry, queued work)."""
    return uuid.uuid4().hex


def instrument_fastapi(app: Any) -> bool:
    """Attach HTTP-level tracing so an API request and the agent nodes it runs
    land in one trace. No-ops without the instrumentation package."""
    if tracer() is None:
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        return True
    except Exception:
        return False
