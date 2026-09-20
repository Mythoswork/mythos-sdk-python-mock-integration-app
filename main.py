import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

from fastapi import Cookie, Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from jose.exceptions import JOSEError  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from mythos_sdk import (  # noqa: E402
    InsufficientFundsError,
    InvalidLaunchTokenError,
    MythosSession,
    MythosConfigError,
    SessionNotFoundError,
    create_handshake_router,
    create_listing_callback_handler,
    decode_session,
    encode_session,
    report_usage,
    require_launch_token,
    verify_launch_token,
)
from mythos_sdk.llm import get_llm_billing_metadata, llm  # noqa: E402

from config import get_config, require_listing_id  # noqa: E402
from listing_ids_store import add_listing_id, get_listing_ids  # noqa: E402
from mythos_client import get_launch_history, get_wallet, launch_app, login  # noqa: E402

CREDITS_PER_CALCULATION = 1
PRODUCER_OPENAI_API_KEY = os.environ.get('PRODUCER_OPENAI_API_KEY')
MODEL_ID = os.environ.get('ALPHA_MODEL_ID', 'openai/gpt-4o-mini')
STANDALONE_MODEL_ID = re.sub(r'^openrouter/', '', MODEL_ID)
STANDALONE_BASE_URL = 'https://openrouter.ai/api/v1'
TMP_DIR = Path(__file__).parent / "tmp"
TEMPLATES_DIR = Path(__file__).parent / "templates"

# This app's own session cookie -- lets any number of routes share one launch/consume
# without re-touching Mythos's single-use launch token, matching the Node mockup's
# lib/session-cookie.ts.
SESSION_COOKIE_NAME = "mythos_session"
SESSION_COOKIE_MAX_AGE_SECONDS = 30 * 60

templates = Jinja2Templates(directory=TEMPLATES_DIR)

app = FastAPI()
app.include_router(create_handshake_router())
app.add_api_route(
    "/.well-known/mythos-listing-registered",
    create_listing_callback_handler(add_listing_id),
    methods=["GET", "POST"],
)


def _bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    return authorization.removeprefix("Bearer ")


def _public_session(session: MythosSession) -> MythosSession:
    return MythosSession(
        userId=session.userId,
        email=session.email,
        displayName=session.displayName,
        listingId=session.listingId,
        sessionJti=session.sessionJti,
    )


_require_launch_token_dep = require_launch_token(resolve_listing_ids=get_listing_ids)


@app.get("/verify-session")
async def verify_session_route(
    response: Response,
    lt: str | None = Query(default=None, alias="lt"),
    mythos_session: str | None = Cookie(default=None),
):
    # The launch token is single-use. If this app's own session cookie is already valid,
    # reuse it instead of consuming `lt` again -- otherwise navigating between this app's
    # own pages/reloads with the same `lt` would fail the second time with "already
    # consumed". But a cookie only proves *some* session was consumed already -- not that
    # it's the one this request's `lt` refers to. A stale-but-still-valid cookie from an
    # earlier launch would otherwise silently swap in for a brand new `lt`, leaving that
    # new session's row permanently unconsumed (a later meter() call then 409s). So an
    # incoming `lt` is only trusted to match the cookie if it decodes to the same
    # sessionJti; anything else falls through and gets consumed fresh below.
    existing_session = decode_session(mythos_session) if mythos_session else None
    same_session_as_cookie = existing_session is not None and lt is None
    if existing_session is not None and lt is not None:
        try:
            incoming = await verify_launch_token(lt, resolve_listing_ids=get_listing_ids)
            same_session_as_cookie = incoming.sessionJti == existing_session.sessionJti
        except Exception:
            # Malformed/expired `lt` alongside a still-good cookie for a different session
            # -- keep trusting the cookie rather than failing a page that may not even
            # need `lt`.
            same_session_as_cookie = True

    if existing_session is not None and same_session_as_cookie:
        return {"success": True, "data": asdict(_public_session(existing_session))}

    session = await _require_launch_token_dep(lt=lt)

    # Encrypts the full session (including the LLM identity token) into this app's own
    # HttpOnly cookie, so any other route can read it back via decode_session() without
    # ever touching Mythos's single-use /consume endpoint again.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=encode_session(session),
        max_age=SESSION_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("ENV") == "production",
        path="/",
    )
    return {"success": True, "data": asdict(_public_session(session))}


class CalculateBody(BaseModel):
    lt: str
    operation: Literal["add", "subtract", "multiply", "divide"]
    a: float
    b: float


class ChatBody(BaseModel):
    message: str


def _compute(operation: str, a: float, b: float) -> float:
    if operation == "add":
        return a + b
    if operation == "subtract":
        return a - b
    if operation == "multiply":
        return a * b
    return a / b


@app.post("/calculate")
async def calculate_route(body: CalculateBody):
    if body.operation == "divide" and body.b == 0:
        raise HTTPException(status_code=400, detail="Division by zero")

    try:
        session = await verify_launch_token(body.lt, resolve_listing_ids=get_listing_ids)
        result = _compute(body.operation, body.a, body.b)
        await report_usage(session.sessionJti, CREDITS_PER_CALCULATION, f"calculator:{body.operation}")
        return {"success": True, "data": {"result": result, "creditsCharged": CREDITS_PER_CALCULATION}}
    except InsufficientFundsError:
        raise HTTPException(status_code=402, detail="Insufficient funds")
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/chat")
async def chat_route(body: ChatBody, mythos_session: str | None = Cookie(default=None)):
    if not PRODUCER_OPENAI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: PRODUCER_OPENAI_API_KEY not set",
        )
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    # Same endpoint either way, matching the Node mockup's /api/chat: with a Mythos
    # session (from the cookie /verify-session set), llm()'s returned client is routed
    # through the Mythos gateway and billed. Without one, llm()'s fallback returns a
    # plain AsyncOpenAI client instead. Only the model id and billing metadata differ.
    session = decode_session(mythos_session) if mythos_session else None
    is_standalone = session is None

    try:
        client = llm(
            session,
            api_key=PRODUCER_OPENAI_API_KEY,
            fallback=AsyncOpenAI(api_key=PRODUCER_OPENAI_API_KEY, base_url=STANDALONE_BASE_URL),
        )
        completion = await client.chat.completions.create(
            model=STANDALONE_MODEL_ID if is_standalone else MODEL_ID,
            messages=[{"role": "user", "content": message}],
            stream=False,
        )
        billing = None if is_standalone else get_llm_billing_metadata(completion)

        return {
            "success": True,
            "data": {
                "reply": completion.choices[0].message.content if completion.choices else None,
                "creditsCharged": billing.get("mythos_charge_credits") if billing else None,
                "mythosCostMicrounits": billing.get("mythos_cost_microunits") if billing else None,
                "mythosPricingSource": billing.get("mythos_pricing_source") if billing else None,
                "billingStatus": billing.get("mythos_billing_status") if billing else None,
            },
        }
    except InsufficientFundsError:
        raise HTTPException(status_code=402, detail="Insufficient funds")
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except MythosConfigError:
        raise HTTPException(status_code=500, detail="Chat service is misconfigured")
    except (InvalidLaunchTokenError, JOSEError):
        raise HTTPException(status_code=401, detail="Invalid launch token")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="Chat request failed")


class LoginBody(BaseModel):
    email: str
    password: str


@app.post("/harness/login")
async def harness_login_route(body: LoginBody):
    try:
        config = get_config()
        result = await login(config.mythos_api_url, body.email, body.password)
        return {"success": True, "data": {"token": result.token, "refreshToken": result.refresh_token, "user": result.user}}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.get("/harness/wallet")
async def harness_wallet_route(bearer_token: str = Depends(_bearer_token)):
    try:
        config = get_config()
        wallet = await get_wallet(config.mythos_api_url, bearer_token)
        return {"success": True, "data": asdict(wallet)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/harness/launch")
async def harness_launch_route(bearer_token: str = Depends(_bearer_token)):
    try:
        config = get_config()
        listing_id = require_listing_id()
        result = await launch_app(config.mythos_api_url, bearer_token, listing_id)
        return {"success": True, "data": asdict(result)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/harness/launch-history")
async def harness_launch_history_route(bearer_token: str = Depends(_bearer_token), limit: int = 20, offset: int = 0):
    try:
        config = get_config()
        result = await get_launch_history(config.mythos_api_url, bearer_token, limit, offset)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/upload")
async def upload_route(file: UploadFile = File(...)):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    filename = Path(file.filename or "upload").name
    dest = TMP_DIR / filename
    contents = await file.read()
    dest.write_bytes(contents)
    return {"success": True, "data": {"filename": filename, "size": len(contents)}}


def _list_tmp_files() -> list[str]:
    if not TMP_DIR.is_dir():
        return []
    return sorted(p.name for p in TMP_DIR.iterdir() if p.is_file())


@app.get("/files")
async def list_files_route():
    return {"success": True, "data": _list_tmp_files()}


@app.get("/download/{filename}")
async def download_file_route(filename: str):
    dest = (TMP_DIR / filename).resolve()
    if dest.parent != TMP_DIR.resolve() or not dest.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(dest, filename=dest.name)


@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    config = get_config()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"email": config.test_user_email, "password": config.test_user_password},
    )


@app.get("/calculator", response_class=HTMLResponse)
async def calculator_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="calculator.html",
        context={"files": _list_tmp_files()},
    )
