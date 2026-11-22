"""WebSocket live activity stream per project."""

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..models import WorkflowEvent
from .deps import bus

ws_router = APIRouter()


@ws_router.websocket("/ws/projects/{project_id}")
async def project_stream(websocket: WebSocket, project_id: str, after_id: int = 0) -> None:
    await websocket.accept()
    session = websocket.app.state.sessions()
    try:
        # Replay anything missed, then stream live.
        last_id = after_id
        for event in session.execute(
            select(WorkflowEvent)
            .where(WorkflowEvent.project_id == project_id, WorkflowEvent.id > after_id)
            .order_by(WorkflowEvent.id)
        ).scalars():
            last_id = event.id
            await websocket.send_json(
                {
                    "id": event.id,
                    "project_id": project_id,
                    "type": event.type,
                    "payload": json.loads(event.payload),
                    "created_at": event.created_at.isoformat(),
                }
            )
        queue = bus(websocket).subscribe(project_id)
        stop = asyncio.Event()

        async def pump() -> None:
            while not stop.is_set():
                data = await queue.get()
                if data["id"] > last_id:
                    await websocket.send_json(data)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_text("pong")
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            stop.set()
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
            bus(websocket).unsubscribe(project_id, queue)
    finally:
        session.close()
