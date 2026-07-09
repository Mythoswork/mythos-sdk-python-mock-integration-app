import json
from pathlib import Path

# ponytail: JSON file on disk, mirrors the Node app's listing-ids-store.ts.
# Not persistence-grade — swap for real storage if this ever stops being a demo.
STORE_PATH = Path(__file__).parent / "data" / "listing-ids.json"


def _read_ids() -> list[str]:
    try:
        return json.loads(STORE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


async def add_listing_id(listing_id: str) -> None:
    ids = _read_ids()
    if listing_id not in ids:
        ids.append(listing_id)
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STORE_PATH.write_text(json.dumps(ids, indent=2))


async def get_listing_ids() -> list[str]:
    return _read_ids()
