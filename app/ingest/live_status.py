"""Is a channel live right now? Two interchangeable checkers.

HelixChecker (preferred) uses the Twitch API and also returns stream id,
title and category. StreamlinkChecker needs no credentials: it asks
`streamlink --json` whether playable streams exist, and uses the metadata
block newer streamlink versions include (title/category) when present.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamInfo:
    stream_id: str | None = None
    title: str | None = None
    category: str | None = None


class LiveChecker(Protocol):
    name: str

    async def check(self, logins: list[str]) -> dict[str, StreamInfo]:
        """Map of lowercase login -> StreamInfo for channels that are live.
        Raises when the check itself failed (unknown != offline)."""
        ...


class HelixChecker:
    name = "twitch-api"

    def __init__(self, client_id: str, client_secret: str, client=None):
        from app.ingest.twitch import HelixClient
        self.client = client or HelixClient(client_id, client_secret)

    async def check(self, logins: list[str]) -> dict[str, StreamInfo]:
        rows = await self.client.get_streams(logins)
        return {login: StreamInfo(stream_id=row.get("id"), title=row.get("title"),
                                  category=row.get("game_name"))
                for login, row in rows.items() if row.get("type", "live") == "live"}


class StreamlinkChecker:
    name = "streamlink"

    def __init__(self, binary: str = "streamlink", timeout: float = 30.0):
        self.binary = binary
        self.timeout = timeout

    async def _probe(self, login: str) -> StreamInfo | None:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "--json", f"twitch.tv/{login}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        try:
            data = json.loads(out or b"{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"streamlink returned non-JSON output for {login}") from exc
        if data.get("streams"):
            meta = data.get("metadata") or {}
            return StreamInfo(stream_id=meta.get("id"), title=meta.get("title"),
                              category=meta.get("category"))
        err = str(data.get("error", ""))
        if "No playable streams" in err or not err:
            return None
        raise RuntimeError(f"streamlink error for {login}: {err}")

    async def check(self, logins: list[str]) -> dict[str, StreamInfo]:
        live: dict[str, StreamInfo] = {}
        for login in logins:
            info = await self._probe(login)
            if info is not None:
                live[login] = info
        return live


def make_checker() -> LiveChecker | None:
    """Twitch API when credentials exist, else streamlink if installed."""
    from app.config import settings
    if settings.TWITCH_CLIENT_ID and settings.TWITCH_CLIENT_SECRET:
        return HelixChecker(settings.TWITCH_CLIENT_ID, settings.TWITCH_CLIENT_SECRET)
    if shutil.which(settings.STREAMLINK_BIN):
        log.info("Auto-monitor using streamlink (no Twitch API credentials): "
                 "title/category only if your streamlink version reports them")
        return StreamlinkChecker(settings.STREAMLINK_BIN)
    log.warning("Auto-monitor disabled: set TWITCH_CLIENT_ID/SECRET or install streamlink")
    return None
