from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.relationship import RelationshipItem, RelationshipTurn
from app.repositories.database import get_engine
from app.services.auth_cache import digest


def fixture_owner(client):
    assert client.get("/api/relationship/state").status_code == 200
    return digest(client.cookies.get("relationship_session"))


def insert_items(owner, count=85, kind="memory"):
    now = datetime.now(UTC)
    ids = []
    with Session(get_engine()) as db:
        for i in range(count):
            item_id = str(uuid4())
            ids.append(item_id)
            db.add(
                RelationshipItem(
                    id=item_id,
                    owner=owner,
                    kind=kind,
                    title=f"Fixture {i:03d}",
                    quote="synthetic quote",
                    status="active" if i % 2 else "completed",
                    created_at=now,
                    revision=1,
                    details={"text": "PRIVATE_SYNTHETIC_TRANSCRIPT\n" * 2000}
                    if kind == "chat_log"
                    else {},
                )
            )
        db.commit()
    return ids


def test_summary_never_contains_transcripts_answers_or_profile_text(backend):
    with TestClient(app) as client:
        owner = fixture_owner(client)
        before = client.get("/api/relationship/state/summary").json()["version"]
        insert_items(owner, 1, "chat_log")
        after = client.get("/api/relationship/state/summary")
        assert after.json()["version"] != before
        assert len(after.content) < 1500
        assert "PRIVATE_SYNTHETIC_TRANSCRIPT" not in after.text
        state = client.get("/api/relationship/state").json()
        assert "items" not in state and state["item_counts"]["chat_log"] == 1
        assert "PRIVATE_SYNTHETIC_TRANSCRIPT" not in str(state)


def test_item_cursor_pagination_handles_equal_timestamps_and_filters(backend):
    with TestClient(app) as client:
        owner = fixture_owner(client)
        ids = insert_items(owner)
        seen, cursor = [], None
        while True:
            params = {"kind": "memory", "limit": 17}
            if cursor:
                params["cursor"] = cursor
            result = client.get("/api/relationship/items", params=params)
            assert result.status_code == 200, result.text
            page = result.json()
            seen += [row["id"] for row in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert len(seen) == len(set(seen)) == 85 and set(seen) == set(ids)
        filtered = client.get(
            "/api/relationship/items",
            params={"kind": "memory", "q": "Fixture 08", "status": "completed"},
        ).json()
        assert len(filtered["items"]) == 3
        assert not client.get(
            "/api/relationship/items", params={"kind": "memory", "q": "%"}
        ).json()["items"]
        assert (
            client.get(
                "/api/relationship/items",
                params={"kind": "memory", "cursor": "invalid"},
            ).status_code
            == 422
        )


def test_raw_content_is_on_demand_and_tenant_checked(backend):
    with TestClient(app) as client, TestClient(app) as other:
        owner = fixture_owner(client)
        item_id = insert_items(owner, 1, "chat_log")[0]
        page = client.get("/api/relationship/items", params={"kind": "chat_log"})
        assert "PRIVATE_SYNTHETIC_TRANSCRIPT" not in page.text
        assert page.json()["items"][0]["details"]["characters"] > 10000
        result = client.get(f"/api/relationship/items/{item_id}/content")
        assert "PRIVATE_SYNTHETIC_TRANSCRIPT" in result.json()["text"]
        assert (
            other.get(f"/api/relationship/items/{item_id}/content").status_code == 404
        )
        assert not other.get(
            "/api/relationship/items", params={"kind": "chat_log"}
        ).json()["items"]


def test_history_cursor_handles_equal_timestamps(backend):
    with TestClient(app) as client:
        owner = fixture_owner(client)
        now = datetime.now(UTC)
        with Session(get_engine()) as db:
            for _ in range(75):
                db.add(
                    RelationshipTurn(
                        id=str(uuid4()),
                        owner=owner,
                        request_id=str(uuid4()),
                        content="fixture",
                        result={},
                        created_at=now,
                    )
                )
            db.commit()
        initial = client.get("/api/relationship/state").json()
        assert len(initial["turns"]) == 20 and initial["history_has_more"]
        seen = [row["id"] for row in initial["turns"]]
        earliest = initial["turns"][0]
        while True:
            rows = client.get(
                "/api/relationship/history",
                params={"before": earliest["created_at"], "before_id": earliest["id"]},
            ).json()
            if not rows:
                break
            seen += [row["id"] for row in rows]
            earliest = rows[0]
        assert len(seen) == len(set(seen)) == 75
