from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.chat import AgentRun, RunEvent
from app.repositories.database import get_engine


def append_event(run_id: UUID, event_type: str, payload: dict) -> None:
    with Session(get_engine()) as db:
        sequence = db.scalar(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(next_event_seq=AgentRun.next_event_seq + 1)
            .returning(AgentRun.next_event_seq)
        )
        if sequence is None:
            raise RuntimeError("Agent run does not exist")
        db.add(
            RunEvent(run_id=run_id, sequence=sequence, type=event_type, payload=payload)
        )
        db.commit()
