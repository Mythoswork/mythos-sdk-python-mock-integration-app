# Mythos Calculator Mockup (Python)

Python (FastAPI) Producer-role harness exercising [`mythos-sdk`](https://github.com/Mythoswork/mythos-sdk) end-to-end against a locally running `mythos-backend`. Functional twin of the Node/Next.js `mythos-calculator-mockup` app — same flow, FastAPI instead of Next.js.

Not production code — a disposable dev/QA harness for validating the SDK's launch → handshake → consume → meter loop, SDK-owned LLM inference, and the dynamic listing-registered callback.

## What it does

- **Harness routes** (`/`, `/harness/*`): login as a Mythos test user, launch the calculator listing, inspect wallet balance and launch history.
- **Producer routes** (`/calculator`, `/api/mythos/session`, `/calculate`, `/chat`): the SDK-integrated side — meters one credit per calculation via `mythos.charge`, and routes LLM inference through `mythos.llm` with observed-cost billing metadata.
- **`/.well-known/mythos-handshake`**: liveness check the backend calls before publishing a listing.
- **`/.well-known/mythos-listing-registered`**: callback the backend POSTs to on listing creation, so the app learns its own dynamic `listing_id` without a manual env var / redeploy.

## Pre-charge confirmation (required)

Before firing any billable action, gate the client-side call to `/calculate` through the
global browser client's `m.confirmCharge()` method. The SDK owns the `postMessage` protocol,
session bootstrap, handshake, and cookie/header transport fallback.

Protocol:

```json
// producer iframe -> window.parent
{ "type": "mythos:confirm-charge", "requestId": "<uuid>", "credits": 1, "reason": "add(1, 2)" }
// window.parent -> producer iframe
{ "type": "mythos:confirm-charge-response", "requestId": "<uuid>", "approved": true }
// on timeout, producer iframe -> window.parent (so the dashboard can close a stale prompt)
{ "type": "mythos:confirm-charge-timeout", "requestId": "<uuid>" }
```

Fail-closed: the calculator charge is skipped (`/calculate` is never called) if the page isn't embedded,
if no matching response arrives within the timeout (default `10000`ms), or if the response is
`approved: false`. This depends entirely on the Mythos dashboard implementing the
`mythos:confirm-charge` listener and confirmation UI on its side. There is no opt-out — **any
dashboard that hasn't implemented the listener yet, or any non-embedded access to
`/calculator` (including this harness's own `Login → Launch` link, which opens the page
directly, not in an iframe), will see every charge silently declined.** Testing the full
confirm → charge path locally requires embedding `/calculator?lt=...` in a page that
implements the listener yourself.

## LLM inference

LLM inference does not use `report_usage` or a client-supplied credit amount. The SDK consumes the
launch token once, reuses its encrypted HttpOnly session cookie (or the session header fallback),
and creates the official async OpenAI client for the request:

```python
from mythos_sdk import create_mythos

mythos = create_mythos()
client = await mythos.llm(request, api_key=producer_openai_api_key)
completion = await client.chat.completions.create(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": message}],
)
billing = mythos.billing(completion)
```

The gateway observes provider usage, settles the charge, and returns billing metadata. The
`kind="llm"` confirmation is an approval gate only: its rough client-side estimate is not billed.
The SDK
stores the identity-bearing session in an encrypted HttpOnly cookie; the browser receives only
the public session fields. The SDK consumes the launch token once and reuses that session across
page changes.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env.local   # fill in MYTHOS_API_URL, TEST_USER_EMAIL, etc.
```

Set `MYTHOS_KEY` in `.env.local` to the current key from **Mythos dashboard → Settings → Mythos key**.
It binds this app to your Producer account so the auto-registration callback only accepts your
own listings. SDK 0.5.0 warns once if it is unset; the key is required from SDK 0.6.0.
Keep the key server-side.

Start the app, then bootstrap a listing (one-shot — creates a published web-app listing pointing back at this app and registers it via the callback):

```bash
.venv/bin/python -m uvicorn main:app --port 8001 --reload --reload-exclude '.venv/*' --reload-exclude '__pycache__/*' --reload-exclude 'data/*'
.venv/bin/python bootstrap.py
```

The mock pins `mythos-sdk[fastapi,llm]==0.5.0` from PyPI, and the page loads the browser client
from `cdn.jsdelivr.net/npm/@mythos-work/sdk@0.4`:

```bash
uv sync
```

To try an unreleased SDK from a sibling checkout, temporarily install it on top (don't commit this):

```bash
uv pip install -e "../mythos-sdk/packages/python[fastapi,llm]"
```

Before SDK 0.5.0 is published, use the P1 sibling checkout in this worktree's own `.venv`:

```bash
uv pip install -e "../mythos-sdk-p1-producer-key/packages/python[fastapi,llm]"
uv run --no-sync uvicorn main:app
```

`--no-sync` preserves the editable install instead of resolving the unpublished PyPI pin.
The project pin remains `==0.5.0`; no permanent local source override is needed.

## Troubleshooting

- `MYTHOS_KEY is malformed`: copy the complete key from the Mythos dashboard into `.env.local` and restart.
- `PRODUCER_MISMATCH`: the key belongs to another account or was regenerated. Copy the current
  key from the correct Producer account's dashboard and redeploy.
