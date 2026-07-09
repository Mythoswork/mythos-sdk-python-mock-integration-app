import os
from dataclasses import dataclass


@dataclass
class AppConfig:
    mythos_api_url: str
    calculator_base_url: str
    mythos_listing_id: str | None
    test_user_email: str
    test_user_password: str


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}. Copy .env.example to .env.local and fill it in.")
    return value


def get_config() -> AppConfig:
    return AppConfig(
        mythos_api_url=_require_env("MYTHOS_API_URL"),
        calculator_base_url=_require_env("CALCULATOR_BASE_URL"),
        mythos_listing_id=os.environ.get("MYTHOS_LISTING_ID") or None,
        test_user_email=_require_env("TEST_USER_EMAIL"),
        test_user_password=_require_env("TEST_USER_PASSWORD"),
    )


def require_listing_id() -> str:
    config = get_config()
    if not config.mythos_listing_id:
        raise RuntimeError("MYTHOS_LISTING_ID is not set. Run bootstrap.py once, then restart the server.")
    return config.mythos_listing_id
