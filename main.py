import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from mythos_sdk import MythosError, create_mythos  # noqa: E402
from mythos_sdk.logger import log_error  # noqa: E402

from config import get_config, require_listing_id  # noqa: E402
from listing_ids_store import add_listing_id, get_listing_ids  # noqa: E402
from mythos_client import get_launch_history, get_wallet, launch_app, login  # noqa: E402

CREDITS_PER_CALCULATION = 1
MODEL_ID = os.environ.get('ALPHA_MODEL_ID', 'openai/gpt-4o-mini')
STANDALONE_MODEL_ID = re.sub(r'^openrouter/', '', MODEL_ID)
STANDALONE_BASE_URL = 'https://openrouter.ai/api/v1'
TMP_DIR = Path(__file__).parent / "tmp"
TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_config()
    yield


app = FastAPI(lifespan=lifespan)
mythos = create_mythos(
    resolve_listing_ids=get_listing_ids,
    on_listing_registered=add_listing_id,
)
app.include_router(mythos.router)


def _bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    return authorization.removeprefix("Bearer ")


class CalculateBody(BaseModel):
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
async def calculate_route(body: CalculateBody, request: Request):
    if body.operation == "divide" and body.b == 0:
        raise HTTPException(status_code=400, detail="Division by zero")

    try:
        result = _compute(body.operation, body.a, body.b)
        await mythos.charge(request, credits=CREDITS_PER_CALCULATION, reason=f"calculator:{body.operation}")
        return {"success": True, "data": {"result": result, "creditsCharged": CREDITS_PER_CALCULATION}}
    except MythosError as err:
        return JSONResponse(
            {"success": False, "error": str(err), "code": err.code},
            status_code=err.http_status,
        )
    except HTTPException:
        raise
    except Exception as err:
        log_error("calculate: unexpected error", err)
        raise HTTPException(status_code=500, detail="Calculation failed") from err


@app.post("/chat")
async def chat_route(body: ChatBody, request: Request):
    config = get_config()
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    try:
        session = await mythos.get_session(request)
        is_standalone = session is None
        client = await mythos.llm(
            request,
            api_key=config.producer_openai_api_key,
            fallback=AsyncOpenAI(api_key=config.producer_openai_api_key, base_url=STANDALONE_BASE_URL),
        )
        completion = await client.chat.completions.create(
            model=STANDALONE_MODEL_ID if is_standalone else MODEL_ID,
            messages=[{"role": "user", "content": message}],
            stream=False,
        )
        billing = None if is_standalone else mythos.billing(completion)

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
    except MythosError as err:
        return JSONResponse(
            {"success": False, "error": str(err), "code": err.code},
            status_code=err.http_status,
        )
    except HTTPException:
        raise
    except Exception as err:
        log_error("chat: upstream request failed", err)
        raise HTTPException(status_code=502, detail="Chat request failed") from err


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
