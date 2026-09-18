"""T-301: GET /api/companies/{id}/realtime/snapshot."""

import uuid

from tests.conftest import unique_company


async def test_snapshot_endpoint(api, db_session):
    company = await unique_company(db_session, "rt-api")
    url = f"/api/companies/{company.id}/realtime/snapshot"
    response = await api.get(url)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["company_id"] == str(company.id)
    assert set(body) == {
        "company_id", "last_seq", "server_time", "agents", "tasks", "recent_events", "kpis",
        "cycle",
    }  # fmt: skip

    assert (await api.get(f"/api/companies/{uuid.uuid4()}/realtime/snapshot")).status_code == 404
    unauthorized = await api.get(url, headers={"Authorization": "Bearer nope"})
    assert unauthorized.status_code == 401


async def test_cors_allows_the_web_app_only(api):
    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }
    allowed = await api.options(
        "/api/companies", headers={"Origin": "http://localhost:3000", **preflight}
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()

    other = await api.options(
        "/api/companies", headers={"Origin": "https://evil.example", **preflight}
    )
    assert "access-control-allow-origin" not in other.headers
