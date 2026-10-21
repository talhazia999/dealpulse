"""One HTTP connection pool shared by every CRM connector, instead of a new client per request."""

import httpx

_http: httpx.AsyncClient | None = None


def shared_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=20)
    return _http
