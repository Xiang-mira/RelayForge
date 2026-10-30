from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from test_api_postgres import client_for

from relayforge.models import RunEvent, StreamEvent
from relayforge.runtime.events import EventStream

pytestmark = pytest.mark.postgres


def test_late_commit_is_published_after_resume_cursor_without_loss(seeded):
    _, sessions, (run_id, _) = seeded
    slow = sessions()
    try:
        slow.begin()
        late = RunEvent(run_id=run_id, type="late.commit", data={"value": 1})
        slow.add(late)
        slow.flush()
        with sessions.begin() as fast:
            early = RunEvent(run_id=run_id, type="early.commit", data={"value": 2})
            fast.add(early)
            fast.flush()
            assert early.id > late.id
        stream = EventStream(sessions)
        first = stream.read(run_id)
        assert [e["type"] for e in first] == ["early.commit"]
        cursor = int(first[-1]["id"])
        slow.commit()
        resumed = EventStream(sessions).read(run_id, cursor)
        assert [e["type"] for e in resumed] == ["late.commit"]
        assert int(resumed[0]["id"]) > cursor
        assert resumed[0]["source_event_id"] < first[0]["source_event_id"]
        assert stream.read(run_id, int(resumed[-1]["id"])) == []
    finally:
        slow.close()


def test_parallel_publishers_do_not_duplicate_source_events_or_lose_rollback_gaps(seeded):
    _, sessions, (run_id, _) = seeded
    with sessions() as rolled_back:
        rolled_back.add(RunEvent(run_id=run_id, type="never.committed", data={}))
        rolled_back.flush()
        rolled_back.rollback()
    with sessions.begin() as session:
        session.add_all(RunEvent(run_id=run_id, type="committed", data={"n": n}) for n in range(20))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: EventStream(sessions).publish(run_id), range(16)))
    rows = EventStream(sessions).read(run_id)
    assert len(rows) == 20
    assert len({e["id"] for e in rows}) == 20
    assert {e["data"]["n"] for e in rows} == set(range(20))
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(StreamEvent)) == 20


def test_sse_reconnect_uses_persisted_cursor_and_validates_run_binding(pg, tmp_path):
    with client_for(pg, tmp_path) as client:
        run_id = client.post("/api/tasks", json={"title": "SSE resume"}).json()["run_id"]
        response = client.get(f"/api/runs/{run_id}/events?once=true")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: runtime\n" in response.text
        history = client.get(f"/api/runs/{run_id}/events/history").json()
        cursor = history["next_cursor"]
        assert all(f"id: {e['id']}\n" in response.text for e in history["events"])
        assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
        resumed = client.get(
            f"/api/runs/{run_id}/events?once=true&after=0", headers={"Last-Event-ID": cursor}
        )
        remaining = client.get(f"/api/runs/{run_id}/events/history?after={cursor}").json()
        assert len(remaining["events"]) == 1
        assert remaining["events"][0]["type"] == "run.cancel_requested"
        assert f"id: {remaining['next_cursor']}\n" in resumed.text
        assert f"id: {cursor}\n" not in resumed.text
        empty = client.get(
            f"/api/runs/{run_id}/events?once=true",
            headers={"Last-Event-ID": remaining["next_cursor"]},
        )
        assert empty.text == ": no new events\n\n"
        other = client.post("/api/tasks", json={"title": "Other run"}).json()["run_id"]
        assert client.get(f"/api/runs/{other}/events?once=true&after={cursor}").status_code == 409
        for invalid in ["-1", "abc", "9223372036854775808"]:
            assert (
                client.get(
                    f"/api/runs/{run_id}/events?once=true", headers={"Last-Event-ID": invalid}
                ).status_code
                == 422
            )
        assert (
            client.get(f"/api/runs/{run_id}/events/history?after=9223372036854775808").status_code
            == 422
        )
        assert client.get("/api/runs/missing/events?once=true").status_code == 404
        schema = client.get("/openapi.json").json()
        assert (
            "text/event-stream"
            in schema["paths"]["/api/runs/{run_id}/events"]["get"]["responses"]["200"]["content"]
        )
