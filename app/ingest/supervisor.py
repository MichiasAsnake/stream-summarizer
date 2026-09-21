"""Audio ingest supervisor (§5.2): streamlink|ffmpeg, sample-counter clock, gap markers, backoff."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class GapEvent:
    t_start: float
    t_end: float
    reason: str
    written: bool = False  # set once a consumer persists it (drain skips written)


LIVE_CMD = "streamlink --stdout twitch.tv/{login} audio_only | ffmpeg -i pipe:0 -f s16le -ar 16000 -ac 1 pipe:1"
REPLAY_VOD_CMD = "streamlink --stdout twitch.tv/videos/{vod_id} audio_only | ffmpeg -i pipe:0 -f s16le -ar 16000 -ac 1 pipe:1"


def build_live_cmd(login: str) -> str:
    return LIVE_CMD.format(login=login)


class IngestSupervisor:
    """Runs the ingest cmd, counts samples for timestamps (§5.2), emits gaps on reconnect."""

    def __init__(self, cmd: str, sample_rate: int = 16000, on_gap=None):
        self.cmd = cmd
        self.sr = sample_rate
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

    async def frames(self, frame_ms: int = 32):
        backoff = 1
        while True:
            try:
                self._proc = await asyncio.create_subprocess_shell(
                    self.cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                assert self._proc.stdout is not None
                gap_start = self.t_now
                first = True
                frame_bytes = int(self.sr * frame_ms / 1000) * 2
                while True:
                    chunk = await self._proc.stdout.read(frame_bytes)
                    if not chunk:
                        break
                    if first and self.sample_index > 0:
                        self._emit_gap(gap_start, self.t_now, "reconnect")
                    first = False
                    self.sample_index += len(chunk) // 2
                    yield chunk
                # clean exit -> reconnect path
                self._emit_gap(gap_start, self.t_now, "exit")
            except Exception as e:  # noqa: BLE001
                self._emit_gap(self.t_now, self.t_now, f"error:{e}")
            await asyncio.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)


async def read_pcm_file(path: str, chunk_ms: int = 32):
    """Replay source for local files: ffmpeg decode to 16k mono, paced by caller."""
    cmd = f'ffmpeg -v error -i "{path}" -f s16le -ar 16000 -ac 1 pipe:1'
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE)
    assert proc.stdout is not None
    n = int(16000 * chunk_ms / 1000) * 2
    while True:
        chunk = await proc.stdout.read(n)
        if not chunk:
            break
        yield chunk
    await proc.wait()
