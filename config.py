import os
from dataclasses import dataclass

# Single source of truth for what one calculation costs: the server-side charge (/calculate),
# the browser confirm dialog (templates/calculator.html) and the listing price (bootstrap.py)
# must always agree. 10,000 credits = $1, so 100 credits = $0.01.
CREDITS_PER_CALCULATION = 100


@dataclass
class AppConfig:
    mythos_api_url: str
    calculator_base_url: str
    mythos_listing_id: str | None
    test_user_email: str
    test_user_password: str
    producer_openai_api_key: str
    mythos_session_secret: str


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}. Copy .env.example to .env.local and fill it in.")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise RuntimeError(f"{name} must be 'true' or 'false'.")
    return normalized == "true"


def get_config() -> AppConfig:
    return AppConfig(
        mythos_api_url=_require_env("MYTHOS_API_URL"),
        calculator_base_url=_require_env("CALCULATOR_BASE_URL"),
        mythos_listing_id=os.environ.get("MYTHOS_LISTING_ID") or None,
        test_user_email=_require_env("TEST_USER_EMAIL"),
        test_user_password=_require_env("TEST_USER_PASSWORD"),
        producer_openai_api_key=_require_env("PRODUCER_OPENAI_API_KEY"),
        mythos_session_secret=_require_env("MYTHOS_SESSION_SECRET"),
    )


def require_listing_id() -> str:
    config = get_config()
    if not config.mythos_listing_id:
        raise RuntimeError("MYTHOS_LISTING_ID is not set. Run bootstrap.py once, then restart the server.")
    return config.mythos_listing_id
