"""Documentation must retain the browser's proxy prefix for schema and API calls."""
import re
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

import httpx
import pytest

from api import main

app = main.app


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "/api/python"])
@pytest.mark.parametrize("page", ["docs", "redoc"])
async def test_docs_schema_and_try_it_out_use_the_same_public_base(prefix, page, monkeypatch):
    monkeypatch.setattr(main, "_orchestrator", SimpleNamespace(get_stats=lambda: {}))
    requested_paths = []

    async def stripping_proxy(scope, receive, send):
        path = scope["path"]
        requested_paths.append(path)
        # Like Vite/Nginx: only this prefix is forwarded to the Python service.
        if prefix and not path.startswith(prefix + "/"):
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"Not an API route"})
            return
        upstream = dict(scope, path=path[len(prefix):])
        upstream["raw_path"] = upstream["path"].encode()
        await app(upstream, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stripping_proxy), base_url="http://medipet.test"
    ) as client:
        response = await client.get(f"{prefix}/{page}")
        assert response.status_code == 200
        pattern = r"url:\s*'([^']+)'" if page == "docs" else r'spec-url="([^"]+)"'
        schema_url = urljoin(str(response.url), re.search(pattern, response.text)[1])
        schema_response = await client.get(schema_url)
        assert schema_response.status_code == 200
        schema = schema_response.json()
        assert "/patients" in schema["paths"]
        assert "/docs" not in schema["paths"]
        server_url = urljoin(schema_url, schema.get("servers", [{"url": "/"}])[0]["url"])
        health_url = server_url.rstrip("/") + "/health"
        assert urlsplit(health_url).path == f"{prefix}/health"
        health = await client.get(health_url)
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert requested_paths == [f"{prefix}/{page}", f"{prefix}/openapi.json", f"{prefix}/health"]
