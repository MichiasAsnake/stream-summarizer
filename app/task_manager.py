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

    _tasks: dict[int, ManagedTask] = {}

    @classmethod
    def get(cls, sid: int) -> ManagedTask | None:
        return cls._tasks.get(sid)

    @classmethod
    def set(cls, sid: int, mt: ManagedTask) -> None:
        cls._tasks[sid] = mt

    @classmethod
    def remove(cls, sid: int) -> None:
        cls._tasks.pop(sid, None)

    @classmethod
    def has_running(cls, sid: int) -> bool:
        mt = cls._tasks.get(sid)
        return mt is not None and mt.status == "running" and not mt.task.done()

    @classmethod
    def stop(cls, sid: int) -> None:
        mt = cls._tasks.get(sid)
        if mt and not mt.task.done():
            mt.status = "stopped"
            mt.task.cancel()
            mt.task.add_done_callback(lambda t: cls.remove(sid))
