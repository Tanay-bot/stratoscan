"""Prometheus-format metrics for Stratoscan (stdlib-only).

Exposes a ``GET /metrics`` endpoint in Prometheus text format (the format
Prometheus scrapes natively) with near-zero marginal cost:

  - ``stratoscan_http_requests_total`` — counter, labelled by method,
    route template and status code.
  - ``stratoscan_http_request_duration_seconds`` — histogram of request
    latency, labelled by method + route (explicit buckets, cumulative).
  - ``stratoscan_uptime_seconds`` — process uptime gauge.
  - ``stratoscan_job_stale`` / ``stratoscan_scheduler_alive`` — live
    background-job health gauges (computed at most once per interval so a
    scrape never wakes a suspended Neon DB).

No third-party dependency: the text format is simple, so the exporter is
~100 lines of stdlib. The ASGI middleware (:class:`MetricsMiddleware`)
wraps the app OUTSIDE the rate limiter so rejected 429s are counted too.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Dict, Optional, Tuple

from src.config import settings

logger = logging.getLogger(__name__)

# Cumulative histogram buckets (seconds). ``+Inf`` is always implicit as
# the last bucket (every request lands in it), which is what Prometheus
# requires for a valid histogram.
_BUCKETS: Tuple[float, ...] = (
    0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"),
)

# Job-health gauges are recomputed at most this often — /metrics is scraped
# every ~15s, and each recompute would otherwise open a Neon connection,
# keeping the serverless compute awake 24/7 (auto-suspend design).
_JOB_GAUGE_TTL_SECONDS: float = 300.0


class MetricsRegistry:
    """Thread-safe registry of counters + a latency histogram."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: Dict[Tuple[str, str, int], int] = defaultdict(int)
        self._latency_sum: Dict[Tuple[str, str], float] = defaultdict(float)
        self._latency_count: Dict[Tuple[str, str], int] = defaultdict(int)
        self._bucket_hits: Dict[Tuple[str, str, int], int] = defaultdict(int)
        self._start: float = time.time()

    def record_request(
        self,
        method: str,
        route: str,
        status: int,
        latency_seconds: float,
    ) -> None:
        """Record one HTTP request's method/route/status + latency."""
        with self._lock:
            key = (method, route, int(status))
            self._requests[key] += 1
            rkey = (method, route)
            self._latency_sum[rkey] += latency_seconds
            self._latency_count[rkey] += 1
            for i, bound in enumerate(_BUCKETS):
                if latency_seconds <= bound:
                    self._bucket_hits[(method, route, i)] += 1
                    break

    def render(self, extra_gauges: Optional[Dict[str, float]] = None) -> str:
        """Render the registry as a Prometheus text-format exposition.

        Snapshots ALL mutable state under the lock first: a Prometheus
        scrape races with live request recording, and iterating the dicts
        while another thread resizes them would raise ``RuntimeError:
        dictionary changed size during iteration`` (or a defaultdict
        ``__getitem__`` insert mid-iteration). Rendering from a consistent
        snapshot keeps /metrics stable under real traffic.
        """
        with self._lock:
            requests = list(self._requests.items())
            latency_count = dict(self._latency_count)
            latency_sum = dict(self._latency_sum)
            bucket_hits = dict(self._bucket_hits)

        lines: list[str] = []

        lines.append(
            "# HELP stratoscan_http_requests_total Total HTTP requests by method, route, status."
        )
        lines.append("# TYPE stratoscan_http_requests_total counter")
        for (method, route, status), count in sorted(requests):
            lines.append(
                f'stratoscan_http_requests_total{{method="{method}",'
                f'route="{route}",status="{status}"}} {count}'
            )

        lines.append(
            "# HELP stratoscan_http_request_duration_seconds "
            "Request latency histogram (seconds)."
        )
        lines.append("# TYPE stratoscan_http_request_duration_seconds histogram")
        for (method, route), _count in sorted(latency_count.items()):
            for i, bound in enumerate(_BUCKETS):
                hits = bucket_hits.get((method, route, i), 0)
                lines.append(
                    f'stratoscan_http_request_duration_seconds_bucket{{method="{method}",'
                    f'route="{route}",le="{bound}"}} {hits}'
                )
            lines.append(
                f'stratoscan_http_request_duration_seconds_sum{{method="{method}",'
                f'route="{route}"}} {latency_sum.get((method, route), 0.0):.9f}'
            )
            lines.append(
                f'stratoscan_http_request_duration_seconds_count{{method="{method}",'
                f'route="{route}"}} {latency_count[(method, route)]}'
            )

        lines.append("# HELP stratoscan_uptime_seconds Process uptime in seconds.")
        lines.append("# TYPE stratoscan_uptime_seconds gauge")
        lines.append(f"stratoscan_uptime_seconds {time.time() - self._start:.1f}")

        for name, value in sorted((extra_gauges or {}).items()):
            lines.append(f"{name} {value}")

        return "\n".join(lines) + "\n"


# Module-level singleton shared by the middleware + endpoint.
registry = MetricsRegistry()


class MetricsMiddleware:
    """ASGI middleware that records every HTTP request into the registry.

    Registered AFTER the rate-limit middleware so it sits OUTERMOST and
    therefore counts requests that the rate limiter rejects (429s) too.
    """

    def __init__(self, app) -> None:
        self.app = app
        # Exact route templates from the FastAPI router, so /stocks/AAPL
        # groups under the /stocks/{symbol} template rather than exploding
        # into thousands of distinct series.
        self._templates: set = set()
        if hasattr(app, "routes"):
            for route in app.routes:
                path = getattr(route, "path", None)
                if isinstance(path, str) and path.startswith("/"):
                    self._templates.add(path)

    def _route_label(self, path: str) -> str:
        """Map a concrete path to its route template (or first segment).

        Exact hits win; then a segment-shape match against the known
        templates (``/stocks/AAPL`` → ``/stocks/{symbol}``); then a
        first-segment fallback so an unmapped ``/foo/<id>`` path can't
        create a per-id label explosion.
        """
        if path in self._templates:
            return path
        segments = path.split("/")
        for tpl in self._templates:
            tsegs = tpl.split("/")
            if len(tsegs) != len(segments):
                continue
            if all(
                ts.startswith("{") and ts.endswith("}") or ts == s
                for ts, s in zip(tsegs, segments)
            ):
                return tpl
        return "/" + segments[1] if len(segments) >= 2 else path

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            from starlette.requests import Request as _Req

            request = _Req(scope, receive)
            route = self._route_label(request.url.path)
            method = request.method or "GET"
        except Exception:  # pragma: no cover — defensive, never block
            route, method = "unknown", "GET"

        start = time.perf_counter()
        status = 500
        sent = False

        async def send_wrapper(message) -> None:
            nonlocal status, sent
            sent = True
            if message["type"] == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            # Re-raise AFTER recording (a 500 with no response still counts).
            registry.record_request(method, route, status, time.perf_counter() - start)
            raise
        else:
            if not sent:  # app produced no response — count as 500
                status = 500
            registry.record_request(method, route, status, time.perf_counter() - start)


# ── Job-health gauges (throttled to protect Neon auto-suspend) ────────────────

_job_gauge_lock = threading.Lock()
_job_gauge_cache: Dict[str, object] = {"at": 0.0, "gauges": {}}


def job_health_gauges() -> Dict[str, float]:
    """Live background-job gauges, recomputed at most every TTL seconds."""
    if not settings.metrics_enabled:
        return {}
    now = time.time()
    with _job_gauge_lock:
        if now - float(_job_gauge_cache["at"]) < _JOB_GAUGE_TTL_SECONDS:
            return dict(_job_gauge_cache["gauges"])  # type: ignore[arg-type]
        try:
            from src.job_monitor import JobMonitor, get_job_monitor_store

            summary = JobMonitor(get_job_monitor_store()).summary()
            gauges: Dict[str, float] = {}
            for job in summary.get("jobs", []):
                gauges[
                    f'stratoscan_job_stale{{job="{job.get("job_name", "?")}"}}'
                ] = (1.0 if job.get("stale") else 0.0)
            gauges["stratoscan_scheduler_alive"] = (
                1.0 if summary.get("scheduler", {}).get("alive") else 0.0
            )
        except Exception as e:  # never let metrics break a scrape
            logger.debug("Job-health gauges unavailable: %s", e)
            gauges = {}
        _job_gauge_cache["at"] = now
        _job_gauge_cache["gauges"] = gauges
        return dict(gauges)


def render_metrics() -> str:
    """Full /metrics payload: request registry + job-health gauges."""
    return registry.render(job_health_gauges())
