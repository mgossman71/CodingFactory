"""Persisted activity events + in-process fan-out to WebSocket subscribers."""

import asyncio
import json
import logging

from sqlalchemy.orm import Session

from ..models import WorkflowEvent

logger = logging.getLogger("cf.events")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict]]] = {}

    def subscribe(self, project_id: str) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        self._subscribers.setdefault(project_id, set()).add(queue)
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue[dict]) -> None:
        self._subscribers.get(project_id, set()).discard(queue)

    def record(
        self,
        session: Session,
        project_id: str,
        event_type: str,
        payload: dict | None = None,
        event_id: int | None = None,
    ) -> WorkflowEvent:
        """Persist first, then broadcast. DB is the source of truth. `event_id`
        pins the id (only to keep ids monotonic after rows were deleted)."""
        event = WorkflowEvent(
            id=event_id, project_id=project_id, type=event_type, payload=json.dumps(payload or {})
        )
        session.add(event)
        session.flush()
        data = {
            "id": event.id,
            "project_id": project_id,
            "type": event_type,
            "payload": payload or {},
            "created_at": event.created_at.isoformat(),
        }
        for queue in self._subscribers.get(project_id, set()):
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                logger.warning("dropping event for slow WS subscriber (project %s)", project_id)
        return event
