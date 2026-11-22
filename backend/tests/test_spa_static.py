"""SPA static serving: index at /, history fallback for client-side routes."""

from pathlib import Path

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def test_spa_routes_served_when_dist_present(env):
    if not (DIST / "index.html").exists():
        import pytest

        pytest.skip("frontend not built")

    resp = env.client.get("/")
    assert resp.status_code == 200
    assert "<div id=\"root\">" in resp.text

    # Client-side route: must fall back to index.html, not 404 JSON.
    resp = env.client.get("/projects/does-not-exist")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]

    # Path traversal must not escape the dist dir.
    resp = env.client.get("/../../etc/passwd")
    assert resp.status_code == 200  # falls back to index.html, file not served
    assert "root:" not in resp.text

    # API routes still win over the catch-all.
    resp = env.client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
