from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUEST_COUNT = Counter(
    "kb_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "kb_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
)


def _normalize_path(path: str) -> str:
    if path.startswith("/v1/knowledge/items/"):
        return "/v1/knowledge/items/{item_id}"
    return path


async def metrics_middleware(request: Request, call_next: Callable) -> Response:
    path = _normalize_path(request.url.path)
    method = request.method
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        elapsed = time.perf_counter() - start
        REQUEST_LATENCY.labels(method=method, path=path).observe(elapsed)
        REQUEST_COUNT.labels(method=method, path=path, status=str(status)).inc()


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
