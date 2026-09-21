"""Audio ingest supervisor (§5.2): streamlink|ffmpeg, sample-counter clock, gap markers, backoff."""
from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GapEvent:
    t_start: float
    t_end: float
    reason: str
    written: bool = False  # set once a consumer persists it (drain skips written)


LIVE_PIPELINE = (
    'set -o pipefail; streamlink --stdout "twitch.tv/$1" audio_only '
    '| ffmpeg -v error -i pipe:0 -f s16le -ar 16000 -ac 1 pipe:1'
)


def build_live_cmd(login: str) -> tuple[str, ...]:
    """Build a parameterized live command.

    The login is passed as a positional shell parameter, never interpolated
    into shell source. The API validates its Twitch-login shape as a second
    layer of protection.
    """
    return ("bash", "-o", "pipefail", "-c", LIVE_PIPELINE, "stream-summarizer", login)


def build_replay_cmd(source: str) -> tuple[str, ...]:
    """Decode a local file or HTTP(S) URL without invoking a shell."""
    return ("ffmpeg", "-v", "error", "-i", source,
            "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1")


class IngestSupervisor:
    """Runs the ingest cmd, counts samples for timestamps (§5.2), emits gaps on reconnect."""

    def __init__(self, cmd: Sequence[str], sample_rate: int = 16000, on_gap=None,
                 reconnect: bool = True, offline_timeout_seconds: float | None = None):
        if isinstance(cmd, (str, bytes)):
            raise TypeError("ingest command must be an argument sequence, not shell text")
        self.cmd = tuple(str(part) for part in cmd)
        if not self.cmd:
            raise ValueError("ingest command cannot be empty")
        self.sr = sample_rate
        self.reconnect = reconnect
        self.offline_timeout_seconds = offline_timeout_seconds
        self.sample_index = 0
        self.gaps: list[GapEvent] = []
        self._proc: asyncio.subprocess.Process | None = None
        # Synchronous hook fired at detection time (insert+commit immediately).
        # Exceptions never propagate; the periodic drain retries unwritten gaps.
        self.on_gap = on_gap

    def _emit_gap(self, t_start: float, t_end: float, reason: str) -> GapEvent:
        g = GapEvent(t_start, t_end, reason)
        self.gaps.append(g)
        if self.on_gap is not None:
            try:
                self.on_gap(g)
            except Exception:
                pass
        return g

    @property
    def t_now(self) -> float:
        return self.sample_index / self.sr

    async def _stop_process(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()

    async def frames(self, frame_ms: int = 32):
        backoff = 1
        last_audio_at = time.monotonic()
        while True:
            try:
                self._proc = await asyncio.create_subprocess_exec(
                    *self.cmd, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                if self._proc.stdout is None:
                    raise RuntimeError("ingest process has no stdout pipe")
                gap_start = self.t_now
                first = True
                frame_bytes = int(self.sr * frame_ms / 1000) * 2
                while True:
                    if self.offline_timeout_seconds is None:
                        chunk = await self._proc.stdout.read(frame_bytes)
                    else:
                        remaining = (self.offline_timeout_seconds
                                     - (time.monotonic() - last_audio_at))
                        if remaining <= 0:
                            return
                        try:
                            chunk = await asyncio.wait_for(
                                self._proc.stdout.read(frame_bytes), timeout=remaining)
                        except TimeoutError:
                            return
                    if not chunk:
                        break
                    if first and self.sample_index > 0:
                        self._emit_gap(gap_start, self.t_now, "reconnect")
                    first = False
                    last_audio_at = time.monotonic()
                    backoff = 1
                    self.sample_index += len(chunk) // 2
                    yield chunk
                await self._proc.wait()
                if not self.reconnect:
                    return
                self._emit_gap(gap_start, self.t_now, "exit")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._emit_gap(self.t_now, self.t_now, f"error:{e}")
                if not self.reconnect:
                    raise
            finally:
                await self._stop_process()
            if (self.offline_timeout_seconds is not None
                    and time.monotonic() - last_audio_at >= self.offline_timeout_seconds):
                return
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)


async def read_pcm_file(path: str, chunk_ms: int = 32):
    """Replay source for local files: ffmpeg decode to 16k mono, paced by caller."""
    source = str(Path(path).expanduser())
    proc = await asyncio.create_subprocess_exec(
        *build_replay_cmd(source), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    if proc.stdout is None:
        raise RuntimeError("ffmpeg has no stdout pipe")
    n = int(16000 * chunk_ms / 1000) * 2
    while True:
        chunk = await proc.stdout.read(n)
        if not chunk:
            break
        yield chunk
    await proc.wait()
