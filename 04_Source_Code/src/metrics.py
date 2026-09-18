from prometheus_client import Counter, Histogram, start_http_server

TICKETS_TOTAL = Counter(
    "tickets_processed_total",
    "Tickets processed by the triage pipeline",
    ["channel", "route"],
)

PIPELINE_LATENCY = Histogram(
    "pipeline_latency_seconds",
    "End-to-end pipeline latency in seconds",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

GUARDRAIL_BLOCKS = Counter(
    "guardrail_blocks_total",
    "Number of times a guardrail validator blocked a request",
    ["validator"],
)

COORDINATOR_TURNS = Histogram(
    "coordinator_turns_total",
    "Number of coordinator loop turns per ticket",
    buckets=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
)

AGENT_CONFIDENCE = Histogram(
    "agent_confidence_scores",
    "Confidence score distribution per agent",
    ["agent"],
    buckets=[0.50, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0],
)

class MetricsService:
    """Prometheus telemetry service manager (SOLID - Single Responsibility)."""

    def __init__(self, default_port: int = 8001):
        self.default_port = default_port
        self._started = False

    def start_server(self, port: int | None = None) -> None:
        target_port = port if port is not None else self.default_port
        if not self._started:
            start_http_server(target_port)
            self._started = True


_METRICS_SERVICE = MetricsService()


def init_metrics_server(port: int = 8001) -> None:
    """Functional facade for MetricsService."""
    _METRICS_SERVICE.start_server(port)

