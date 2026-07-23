# Mythos Calculator Mockup (Python)

Python (FastAPI) Producer-role harness exercising [`mythos-sdk`](https://github.com/Mythoswork/mythos-sdk) end-to-end against a locally running `mythos-backend`. Functional twin of the Node/Next.js `mythos-calculator-mockup` app — same flow, FastAPI instead of Next.js.

Not production code — a disposable dev/QA harness for validating the SDK's launch → handshake → consume → meter loop, and the dynamic listing-registered callback.

## What it does

- **Harness routes** (`/`, `/harness/*`): login as a Mythos test user, launch the calculator listing, inspect wallet balance and launch history.
- **Producer routes** (`/calculator`, `/verify-session`, `/calculate`): the SDK-integrated side — verifies the launch token once, consumes the session, then meters one credit per calculation via `report_usage`.
- **`/.well-known/mythos-handshake`**: liveness check the backend calls before publishing a listing.
- **`/.well-known/mythos-listing-registered`**: callback the backend POSTs to on listing creation, so the app learns its own dynamic `listing_id` without a manual env var / redeploy.

## Pre-charge confirmation (optional)

For billable actions where the Consumer should explicitly approve a charge before it fires
(e.g. a large or unusual credit spend), gate the client-side call to `/calculate` behind a
`postMessage` round trip with the Mythos dashboard (`window.parent`), instead of calling it
unconditionally. `/calculator`'s page script demonstrates this with a `confirmCharge()` helper,
wired up behind a `requireConfirmation` checkbox in the UI, **checked by default** — unticking
it is an explicit opt-out, not the starting state. Note that the harness's own standalone
`Login → Launch` link opens `/calculator` directly, not embedded in an iframe, so it will hit
the fail-closed path below unless the checkbox is unticked.

Protocol:

```json
// producer iframe -> window.parent
{ "type": "mythos:confirm-charge", "requestId": "<uuid>", "credits": 1, "reason": "add(1, 2)" }
// window.parent -> producer iframe
{ "type": "mythos:confirm-charge-response", "requestId": "<uuid>", "approved": true }
// on timeout, producer iframe -> window.parent (so the dashboard can close a stale prompt)
{ "type": "mythos:confirm-charge-timeout", "requestId": "<uuid>" }
```

Fail-closed: the charge is skipped (`/calculate` is never called) if the page isn't embedded,
if no matching response arrives within the timeout (default `10000`ms), or if the response is
`approved: false`. This depends entirely on the Mythos dashboard implementing the
`mythos:confirm-charge` listener and confirmation UI on its side. Because `requireConfirmation`
defaults to checked, **any dashboard that hasn't implemented the listener yet — or direct
non-embedded access to `/calculator` — will see every charge silently declined**. Untick the
checkbox to fall back to unconditional metering while your dashboard's listener is still in
progress.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env.local   # fill in MYTHOS_API_URL, TEST_USER_EMAIL, etc.
```

Start the app, then bootstrap a listing (one-shot — creates a published web-app listing pointing back at this app and registers it via the callback):

```bash
.venv/bin/python -m uvicorn main:app --port 8001 --reload --reload-exclude '.venv/*' --reload-exclude '__pycache__/*' --reload-exclude 'data/*'
.venv/bin/python bootstrap.py
```

The SDK dependency is installed from a local `file://` path (see `pyproject.toml`) — after pulling SDK changes, reinstall it into this venv:

```bash
pip install --force-reinstall --no-deps "mythos-sdk @ file:///Users/glenn-steven-santoso/git/work/mythos-sdk/packages/python"
```
