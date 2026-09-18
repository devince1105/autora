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
