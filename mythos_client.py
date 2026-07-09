import base64
import json
from dataclasses import dataclass
from typing import Any, Literal

import httpx


@dataclass
class LoginResult:
    token: str
    refresh_token: str
    user: dict[str, Any]


@dataclass
class WalletSummary:
    success: bool
    subscription: int
    topup: int
    total: int
    resets_at: str | None
    current_plan: dict[str, Any] | None


@dataclass
class LaunchResult:
    launch_url: str
    launch_token: str
    expires_at: str
    frame_sandbox: list[str]


async def _parse_json_or_raise(resp: httpx.Response, label: str) -> Any:
    try:
        body = resp.json()
    except Exception:
        body = {}
    if resp.is_error:
        raise RuntimeError(f"{label} failed: HTTP {resp.status_code} {json.dumps(body)}")
    return body


async def login(api_url: str, email: str, password: str) -> LoginResult:
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{api_url}/api/auth/login", json={"email": email, "password": password})
        body = await _parse_json_or_raise(resp, "login")
        return LoginResult(token=body["token"], refresh_token=body["refreshToken"], user=body["user"])


async def get_wallet(api_url: str, bearer_token: str) -> WalletSummary:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{api_url}/api/wallet", headers={"Authorization": f"Bearer {bearer_token}"})
        body = await _parse_json_or_raise(resp, "getWallet")
        return WalletSummary(
            success=body["success"],
            subscription=body["subscription"],
            topup=body["topup"],
            total=body["total"],
            resets_at=body.get("resetsAt"),
            current_plan=body.get("currentPlan"),
        )


async def create_web_app_listing(
    api_url: str,
    bearer_token: str,
    title: str,
    description: str,
    category: str,
    launch_url: str,
    status: Literal["draft", "published"],
    cover_image: str,
    thumbnail_image: str | None = None,
    price_credits: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "category": category,
        "launch_url": launch_url,
        "status": status,
        "cover_image": cover_image,
    }
    if thumbnail_image is not None:
        payload["thumbnail_image"] = thumbnail_image
    if price_credits is not None:
        payload["price_credits"] = price_credits

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{api_url}/api/listings/web-app",
            json=payload,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        body = await _parse_json_or_raise(resp, "createWebAppListing")
        return body["data"]


async def launch_app(api_url: str, bearer_token: str, listing_id: str) -> LaunchResult:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{api_url}/api/apps/{listing_id}/launch",
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        body = await _parse_json_or_raise(resp, "launchApp")
        data = body["data"]
        return LaunchResult(
            launch_url=data["launch_url"],
            launch_token=data["launch_token"],
            expires_at=data["expires_at"],
            frame_sandbox=data["frame_sandbox"],
        )


async def get_launch_history(api_url: str, bearer_token: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{api_url}/api/launch-history",
            params={"limit": limit, "offset": offset},
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        return await _parse_json_or_raise(resp, "getLaunchHistory")


def decode_launch_token_jti(launch_token: str) -> str:
    parts = launch_token.split(".")
    if len(parts) != 3:
        raise ValueError("launch_token is not a valid JWT (expected 3 dot-separated segments)")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    if "jti" not in payload:
        raise ValueError("launch_token payload has no jti claim")
    return payload["jti"]
