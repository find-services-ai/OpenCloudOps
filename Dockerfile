# Multi-stage so the runtime image carries no build toolchain.
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY opencloudops/ ./opencloudops/
RUN pip install --no-cache-dir --target=/build/deps ".[api,otel]"

FROM python:3.12-slim

# curl is here only for the container health check. Nothing in the application
# shells out.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin occ

WORKDIR /app

COPY --from=builder /build/deps /app/deps
COPY opencloudops/ ./opencloudops/
# The contracts are configuration, not code: mount your own over these to make
# the image yours without rebuilding it.
COPY platform/ ./platform/
COPY patterns/ ./patterns/
COPY graphs/ ./graphs/
COPY agent-graph.yaml ./agent-graph.yaml

ENV PYTHONPATH=/app/deps:/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OCC_PLATFORM=/app/platform/platform.yaml \
    OCC_PATTERNS=/app/patterns \
    OCC_GRAPHS=/app/graphs \
    OCC_AGENT_GRAPH=/app/agent-graph.yaml \
    OCC_AUDIT_LOG=/app/logs/audit.jsonl \
    OCC_MEMORY_DIR=/app/memories \
    OTEL_SERVICE_NAME=opencloudops

RUN mkdir -p /app/logs /app/memories && chown -R occ:occ /app/logs /app/memories

EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8081/ready || exit 1

USER occ

# No cloud credentials are expected, wanted, or used. The service proposes; a
# person applies. Mounting a credential here would not enable an apply -- there
# is no apply path -- but it would make the container worth attacking.
CMD ["python", "-m", "uvicorn", "opencloudops.api:app", "--host", "0.0.0.0", "--port", "8081"]
