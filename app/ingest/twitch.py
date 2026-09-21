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
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://id.twitch.tv/oauth2/token", params={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "grant_type": "client_credentials"})
            r.raise_for_status()
            self._token_val = r.json()["access_token"]
            return self._token_val

    async def _get(self, path: str, params) -> dict:
        """GET a Helix endpoint, refreshing the app token once on 401."""
        for attempt in range(2):
            token = await self._token()
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{HELIX}/{path}", params=params,
                                headers={"Client-ID": self.client_id,
                                         "Authorization": f"Bearer {token}"})
            if r.status_code == 401 and attempt == 0:
                self._token_val = None
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    async def get_streams(self, logins: list[str]) -> dict[str, dict]:
        """Live streams keyed by lowercase login; offline logins are absent."""
        live: dict[str, dict] = {}
        for i in range(0, len(logins), 100):  # Helix accepts 100 logins per call
            batch = logins[i:i + 100]
            data = await self._get("streams", [("user_login", lg) for lg in batch])
            for row in data.get("data", []):
                live[row["user_login"].lower()] = row
        return live

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
