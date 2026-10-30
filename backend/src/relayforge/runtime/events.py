import json

from sqlalchemy import select, text

from relayforge.models import RunEvent, StreamEvent


class EventStream:
    def __init__(self, sessions):
        self.sessions = sessions

    def publish(self, run_id: str):
        # Source IDs can commit out of order. Publish only visible committed rows
        # in a separate serialized transaction, without locking producer rows.
        with self.sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(91293417)"))
            source = session.scalars(
                select(RunEvent)
                .outerjoin(StreamEvent, StreamEvent.source_event_id == RunEvent.id)
                .where(RunEvent.run_id == run_id, StreamEvent.id.is_(None))
                .order_by(RunEvent.id)
                .limit(500)
            ).all()
            for event in source:
                session.add(StreamEvent(source_event_id=event.id, run_id=run_id))

    def validate_cursor(self, run_id: str, cursor: int) -> bool:
        if cursor == 0:
            return True
        with self.sessions() as session:
            row = session.get(StreamEvent, cursor)
            return bool(row and row.run_id == run_id)

    def read(self, run_id: str, after: int = 0, limit: int = 200) -> list[dict]:
        self.publish(run_id)
        with self.sessions() as session:
            rows = session.execute(
                select(StreamEvent, RunEvent)
                .join(RunEvent, RunEvent.id == StreamEvent.source_event_id)
                .where(StreamEvent.run_id == run_id, StreamEvent.id > after)
                .order_by(StreamEvent.id)
                .limit(limit)
            ).all()
            return [
                {
                    "id": str(published.id),
                    "source_event_id": event.id,
                    "run_id": event.run_id,
                    "effect_id": event.effect_id,
                    "type": event.type,
                    "timestamp": event.created_at.isoformat(),
                    "data": event.data,
                }
                for published, event in rows
            ]

    @staticmethod
    def frame(event: dict) -> str:
        return (
            f"id: {event['id']}\nevent: runtime\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        )
