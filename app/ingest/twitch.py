"""Twitch Helix polling (§5.1): GET /streams, title/category tracking."""
from __future__ import annotations

import httpx

HELIX = "https://api.twitch.tv/helix"


class HelixClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._token_val: str | None = None

    async def _token(self) -> str:
        if self._token_val:
            return self._token_val
        async with httpx.AsyncClient() as c:
            r = await c.post("https://id.twitch.tv/oauth2/token", params={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "grant_type": "client_credentials"})
            r.raise_for_status()
            self._token_val = r.json()["access_token"]
            return self._token_val

    async def get_stream(self, login: str) -> dict | None:
        token = await self._token()
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{HELIX}/streams", params={"user_login": login},
                            headers={"Client-ID": self.client_id, "Authorization": f"Bearer {token}"})
            r.raise_for_status()
            data = r.json().get("data", [])
            return data[0] if data else None

    async def get_user_id(self, login: str) -> str | None:
        token = await self._token()
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{HELIX}/users", params={"login": login},
                            headers={"Client-ID": self.client_id, "Authorization": f"Bearer {token}"})
            r.raise_for_status()
            data = r.json().get("data", [])
            return data[0]["id"] if data else None
