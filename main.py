import os
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from jose.exceptions import JOSEError  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from mythos_sdk import (  # noqa: E402
    InsufficientFundsError,
    InvalidLaunchTokenError,
    get_llm_billing_metadata,
    llm,
    MythosSession,
    MythosConfigError,
    SessionNotFoundError,
    create_handshake_router,
    create_listing_callback_handler,
    report_usage,
    require_launch_token,
    verify_launch_token,
)

from config import get_config, require_listing_id  # noqa: E402
from listing_ids_store import add_listing_id, get_listing_ids  # noqa: E402
from mythos_client import get_launch_history, get_wallet, launch_app, login  # noqa: E402
from mythos_session_store import get_mythos_session, remember_mythos_session  # noqa: E402

CREDITS_PER_CALCULATION = 1
PRODUCER_OPENAI_API_KEY = os.environ.get('PRODUCER_OPENAI_API_KEY')
MODEL_ID = os.environ.get('ALPHA_MODEL_ID', 'openai/gpt-4o-mini')
TMP_DIR = Path(__file__).parent / "tmp"
TEMPLATES_DIR = Path(__file__).parent / "templates"

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


@app.get("/verify-session")
async def verify_session_route(
    lt: str = Query(..., alias="lt"),
    session: MythosSession = Depends(require_launch_token(resolve_listing_ids=get_listing_ids)),
):
    remember_mythos_session(lt, session)
    public_session = MythosSession(
        userId=session.userId,
        email=session.email,
        displayName=session.displayName,
        listingId=session.listingId,
        sessionJti=session.sessionJti,
    )
    return {"success": True, "data": asdict(public_session)}


class CalculateBody(BaseModel):
    lt: str
    operation: Literal["add", "subtract", "multiply", "divide"]
    a: float
    b: float


class ChatBody(BaseModel):
    lt: str
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
async def chat_route(body: ChatBody):
    if not PRODUCER_OPENAI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: PRODUCER_OPENAI_API_KEY not set",
        )
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    try:
        verified_session = await verify_launch_token(body.lt, resolve_listing_ids=get_listing_ids)
        session = get_mythos_session(body.lt)
        if session is None or session.sessionJti != verified_session.sessionJti:
            raise HTTPException(status_code=401, detail="Mythos session is not initialized")

        client = llm(session, api_key=PRODUCER_OPENAI_API_KEY)
        completion = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": body.message.strip()}],
            stream=False,
        )
        billing = get_llm_billing_metadata(completion)

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
