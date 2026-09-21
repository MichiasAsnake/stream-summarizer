"""Task manager: tracks running pipeline tasks per session so endpoints can
launch, stop, and avoid duplicate tasks. Singleton held by the app.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class ManagedTask:
    task: asyncio.Task
    cfg: Any
    status: str = "running"  # running|stopped|failed


class TaskManager:
    """One entry per active session_id. Accessed by monitor/replay endpoints."""

    def __init__(self) -> None:
        self._tasks: dict[int, ManagedTask] = {}

    def get(self, sid: int) -> ManagedTask | None:
        return self._tasks.get(sid)

    def set(self, sid: int, mt: ManagedTask) -> None:
        self._tasks[sid] = mt
        mt.task.add_done_callback(lambda task: self._finished(sid, task))

    def _finished(self, sid: int, task: asyncio.Task) -> None:
        mt = self._tasks.pop(sid, None)
        if mt is not None and not task.cancelled() and task.exception() is not None:
            mt.status = "failed"

    def remove(self, sid: int) -> None:
        self._tasks.pop(sid, None)

    def has_running(self, sid: int) -> bool:
        mt = self._tasks.get(sid)
        return mt is not None and mt.status == "running" and not mt.task.done()

    async def stop(self, sid: int) -> None:
        mt = self._tasks.get(sid)
        if mt and not mt.task.done():
            mt.status = "stopped"
            mt.task.cancel()
            try:
                await mt.task
            except asyncio.CancelledError:
                pass
        self.remove(sid)
