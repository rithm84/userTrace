"""Supabase Auth helpers for the integration run.

Tests create real users through the Auth admin API with the project's secret key
and sign them in with the publishable key, so JWT verification, membership
resolution, and RLS are exercised against genuine access tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import httpx

__all__ = ["SignedInUser", "create_user", "delete_user", "sign_in"]

_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class SignedInUser:
    """One real Supabase Auth user created for the test run."""

    auth_user_id: UUID
    email: str
    password: str
    access_token: str
    identity_provider: str
    identity_provider_id: str


def create_user(
    *, supabase_url: str, secret_key: str, email: str, password: str
) -> UUID:
    """Create a confirmed Auth user and return its subject identifier."""
    response = httpx.post(
        f"{supabase_url.rstrip('/')}/auth/v1/admin/users",
        headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
        json={"email": email, "password": password, "email_confirm": True},
        timeout=_TIMEOUT,
    )
    if response.status_code >= 300:
        raise RuntimeError(
            f"Supabase admin user creation failed with {response.status_code}: "
            f"{response.text}"
        )
    return UUID(str(response.json()["id"]))


def sign_in(
    *, supabase_url: str, publishable_key: str, email: str, password: str
) -> tuple[str, str, str]:
    """Sign a user in and return the access token and its provider identity."""
    response = httpx.post(
        f"{supabase_url.rstrip('/')}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": publishable_key},
        json={"email": email, "password": password},
        timeout=_TIMEOUT,
    )
    if response.status_code >= 300:
        raise RuntimeError(
            f"Supabase password sign-in failed with {response.status_code}: "
            f"{response.text}"
        )
    body = response.json()
    identities = body["user"].get("identities") or []
    if not identities:
        raise RuntimeError("the signed-in user has no provider identity")
    identity = identities[0]
    return (
        str(body["access_token"]),
        str(identity["provider"]),
        str(identity["identity_data"]["sub"]),
    )


def delete_user(*, supabase_url: str, secret_key: str, auth_user_id: UUID) -> None:
    """Delete one Auth user created by the test run."""
    response = httpx.delete(
        f"{supabase_url.rstrip('/')}/auth/v1/admin/users/{auth_user_id}",
        headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
        timeout=_TIMEOUT,
    )
    if response.status_code >= 300 and response.status_code != 404:
        raise RuntimeError(
            f"Supabase admin user deletion failed with {response.status_code}: "
            f"{response.text}"
        )
