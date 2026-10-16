"""Verify access tokens with the configured Supabase Auth server, never browser claims."""
import os
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Identity:
    id: UUID
    email: str | None


async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Identity:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    if not url or not key:
        raise HTTPException(503, "Authentication is not configured")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{url}/auth/v1/user", headers={
                "apikey": key, "Authorization": f"Bearer {credentials.credentials}",
            })
        if response.status_code in (401, 403):
            raise HTTPException(401, "Session invalid or expired", headers={"WWW-Authenticate": "Bearer"})
        if response.status_code != 200:
            raise HTTPException(503, "Authentication temporarily unavailable")
        user = response.json()
        if user.get("is_anonymous") or user.get("role") != "authenticated":
            raise HTTPException(401, "Sign in required")
        return Identity(UUID(user["id"]), user.get("email"))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(503, "Authentication temporarily unavailable") from exc
