"""Prometheus metrics shared by the API process."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "intrv_http_requests_total",
    "HTTP requests handled, by route template and status code.",
    ["method", "route", "status"],
)

HTTP_LATENCY = Histogram(
    "intrv_http_request_duration_seconds",
    "HTTP request latency, by route template.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

READINESS_CHECK_FAILURES = Counter(
    "intrv_readiness_check_failures_total",
    "Failed dependency readiness checks.",
    ["dependency"],
)
