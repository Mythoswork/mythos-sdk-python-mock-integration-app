import asyncio
import re
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent / ".env.local"
load_dotenv(ENV_PATH)

from config import get_config  # noqa: E402
from mythos_client import create_web_app_listing, login  # noqa: E402


async def main() -> None:
    config = get_config()

    print(f"Logging in as {config.test_user_email} against {config.mythos_api_url}...")
    result = await login(config.mythos_api_url, config.test_user_email, config.test_user_password)

    print("Creating web-app listing (status: published)...")
    listing = await create_web_app_listing(
        config.mythos_api_url,
        result.token,
        title="Mythos Calculator Mockup (Python)",
        description="Python calculator app exercising launch, handshake, consume, and metering.",
        category="Web Development",
        launch_url=f"{config.calculator_base_url}/calculator",
        status="published",
        cover_image="https://example.com/calculator-cover.png",
        price_credits=1,
    )

    listing_id = listing["listing_id"]
    print(f"Listing created: {listing_id}")

    env_content = ENV_PATH.read_text()
    if "MYTHOS_LISTING_ID=" in env_content:
        updated = re.sub(r"MYTHOS_LISTING_ID=.*", f"MYTHOS_LISTING_ID={listing_id}", env_content)
    else:
        updated = f"{env_content}\nMYTHOS_LISTING_ID={listing_id}\n"
    ENV_PATH.write_text(updated)

    print(".env.local updated with MYTHOS_LISTING_ID.")
    print("Restart the server (uvicorn main:app --port 8001) to pick up the new listing ID.")


if __name__ == "__main__":
    asyncio.run(main())
