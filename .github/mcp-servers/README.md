# IaC Agent Platform — MCP Server Framework

Four Model Context Protocol (MCP) servers provide AI agents with direct access to operational data sources. Each server implements a standard interface so backends can be swapped.

## Server Architecture

| Generic Name | Default Backend | Alternatives | Purpose |
|---|---|---|---|
| `cloud-logs` | AWS CloudWatch | GCP Logging, Azure Monitor | CI/CD logs, service logs, build output |
| `issue-tracker` | Atlassian JIRA | Linear, GitHub Issues, Azure DevOps | Story tracking, deployment comments |
| `observability` | New Relic | Datadog, Grafana Cloud, CloudWatch | APM, traces, container metrics, alerts |
| `log-aggregator` | OpenSearch | Elasticsearch, Loki, Datadog Logs | Historical log search, cross-service correlation |

## Interface Contracts

Every MCP server MUST implement these tool signatures. The agent layer calls these tools generically — swapping the backend requires zero agent changes.

### cloud-logs

```python
@tool("get_log_events")
def get_log_events(log_group: str, filter_pattern: str = "", hours_back: int = 1) -> str:
    """Tail/search logs from a specific log group."""

@tool("query_logs")
def query_logs(log_group: str, query: str, hours_back: int = 1) -> str:
    """Run a structured query (CloudWatch Insights / GCP filter / KQL)."""

@tool("extract_errors")
def extract_errors(log_group: str, hours_back: int = 6) -> str:
    """Extract and classify errors from CI/CD build logs."""

@tool("get_build_log")
def get_build_log(project_name: str, build_id: str = "latest") -> str:
    """Get CI/CD build output and status."""

@tool("list_log_groups")
def list_log_groups(prefix: str = "") -> str:
    """Discover available log groups."""
```

### issue-tracker

```python
@tool("get_issue")
def get_issue(issue_key: str) -> str:
    """Get full issue details including acceptance criteria."""

@tool("search_issues")
def search_issues(query: str) -> str:
    """Search issues via JQL/filter/query language."""

@tool("add_comment")
def add_comment(issue_key: str, comment: str) -> str:
    """Post a comment/update to an issue."""

@tool("get_transitions")
def get_transitions(issue_key: str) -> str:
    """Get available status transitions for an issue."""
```

### observability

```python
@tool("triage_service")
def triage_service(service_name: str, environment: str) -> str:
    """Full health dashboard: errors, throughput, latency, deployments."""

@tool("get_app_health")
def get_app_health(app_name: str, minutes_back: int = 30) -> str:
    """Error rate + throughput over time window."""

@tool("get_service_errors")
def get_service_errors(app_name: str, minutes_back: int = 30) -> str:
    """Recent error traces with stack traces."""

@tool("get_database_performance")
def get_database_performance(app_name: str) -> str:
    """Slow queries, connection pool, DB call time."""

@tool("get_container_health")
def get_container_health(cluster_name: str, service_name: str) -> str:
    """CPU/mem utilization, restart count, task/pod count."""

@tool("get_deployments")
def get_deployments(app_name: str, hours_back: int = 24) -> str:
    """Deployment markers for correlation."""

@tool("get_infra_metrics")
def get_infra_metrics(hostname_like: str) -> str:
    """Host-level CPU/mem/disk/network."""

@tool("run_query")
def run_query(query: str) -> str:
    """Custom query (NRQL / PromQL / DQL / LogQL)."""
```

### log-aggregator

```python
@tool("search_logs")
def search_logs(index: str, query_string: str, hours_back: int = 24) -> str:
    """Full-text search across log indices."""

@tool("get_service_errors")
def get_service_errors(service_name: str, hours_back: int = 24) -> str:
    """Error/FATAL logs for a specific service."""

@tool("list_indices")
def list_indices(pattern: str = "*") -> str:
    """Discover available log indices."""
```

## Setup

### 1. Install dependencies

```bash
pip install -r .github/mcp-servers/cloud-logs/requirements.txt
pip install -r .github/mcp-servers/issue-tracker/requirements.txt
pip install -r .github/mcp-servers/observability/requirements.txt
pip install -r .github/mcp-servers/log-aggregator/requirements.txt
```

### 2. Configure environment variables

Copy `.github/mcp-servers/.env.example` to `.github/.env-private` and fill in:

```bash
# Cloud Logs (AWS CloudWatch example)
AWS_REGION=us-east-1
AWS_PROFILE=my-profile

# Issue Tracker (JIRA example)
ISSUE_TRACKER_URL=https://your-org.atlassian.net
ISSUE_TRACKER_USERNAME=your.email@company.com
ISSUE_TRACKER_API_TOKEN=your-token

# Observability (New Relic example)
OBSERVABILITY_API_KEY=NRAK-...
OBSERVABILITY_ACCOUNT_ID=1234567

# Log Aggregator (OpenSearch example)
LOG_AGGREGATOR_ENDPOINT=https://your-domain.us-east-1.es.amazonaws.com
```

### 3. VS Code MCP configuration

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "cloud-logs": {
      "command": "python",
      "args": [".github/mcp-servers/cloud-logs/server.py"],
      "env": { "AWS_PROFILE": "${config:aws.profile}" }
    },
    "issue-tracker": {
      "command": "python",
      "args": [".github/mcp-servers/issue-tracker/server.py"]
    },
    "observability": {
      "command": "python",
      "args": [".github/mcp-servers/observability/server.py"]
    },
    "log-aggregator": {
      "command": "python",
      "args": [".github/mcp-servers/log-aggregator/server.py"]
    }
  }
}
```

## Adding a Custom Backend

To swap a backend (e.g., Datadog instead of New Relic):

1. Create `mcp-servers/observability/backends/datadog.py`
2. Implement the same tool signatures
3. Set `OBSERVABILITY_BACKEND=datadog` in `.env-private`
4. The server.py routes to the correct backend

No agent or skill changes needed.
