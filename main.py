from dataclasses import asdict
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env.local")

from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from mythos_sdk import (  # noqa: E402
    InsufficientFundsError,
    MythosSession,
    SessionNotFoundError,
    create_handshake_router,
    create_listing_callback_router,
    report_usage,
    require_launch_token,
    verify_launch_token,
)

from config import get_config, require_listing_id  # noqa: E402
from listing_ids_store import add_listing_id, get_listing_ids  # noqa: E402
from mythos_client import get_launch_history, get_wallet, launch_app, login  # noqa: E402

CREDITS_PER_CALCULATION = 1

app = FastAPI()
app.include_router(create_handshake_router())
app.include_router(create_listing_callback_router(add_listing_id))


def _bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    return authorization.removeprefix("Bearer ")


@app.get("/verify-session")
async def verify_session_route(
    session: MythosSession = Depends(require_launch_token(resolve_listing_ids=get_listing_ids)),
):
    return {"success": True, "data": asdict(session)}


class CalculateBody(BaseModel):
    lt: str
    operation: Literal["add", "subtract", "multiply", "divide"]
    a: float
    b: float


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


_HARNESS_HTML = """
<!doctype html><html><body style="font-family:monospace;max-width:640px;margin:2rem auto">
<h2>Mythos Calculator Mockup (Python)</h2>
<button onclick="run()">Login &rarr; Launch</button>
<pre id="out"></pre>
<script>
async function run() {
  const out = document.getElementById('out');
  const login = await fetch('/harness/login', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({email: '__EMAIL__', password: '__PASSWORD__'})}).then(r => r.json());
  out.textContent = 'login: ' + JSON.stringify(login, null, 2);
  if (!login.success) return;
  const token = login.data.token;
  const wallet = await fetch('/harness/wallet', {headers:{Authorization: 'Bearer ' + token}}).then(r => r.json());
  out.textContent += '\\nwallet: ' + JSON.stringify(wallet, null, 2);
  const launch = await fetch('/harness/launch', {method:'POST', headers:{Authorization: 'Bearer ' + token}}).then(r => r.json());
  out.textContent += '\\nlaunch: ' + JSON.stringify(launch, null, 2);
  if (launch.success) {
    out.innerHTML += '<br><a href="/calculator?lt=' + encodeURIComponent(launch.data.launch_token) + '">Open calculator</a>';
  }
}
</script>
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
async def index_page():
    config = get_config()
    return _HARNESS_HTML.replace("__EMAIL__", config.test_user_email).replace("__PASSWORD__", config.test_user_password)


_CALCULATOR_HTML = """
<!doctype html><html><body style="font-family:monospace;max-width:640px;margin:2rem auto">
<h2>Calculator</h2>
<pre id="session"></pre>
<input id="a" type="number" value="2"> <select id="op">
<option value="add">+</option><option value="subtract">-</option>
<option value="multiply">*</option><option value="divide">/</option>
</select> <input id="b" type="number" value="3">
<button onclick="calc()">=</button>
<pre id="out"></pre>
<script>
const lt = new URLSearchParams(location.search).get('lt');
fetch('/verify-session?lt=' + encodeURIComponent(lt)).then(r => r.json()).then(d => {
  document.getElementById('session').textContent = 'verify-session: ' + JSON.stringify(d, null, 2);
  if (d.success) {
    window.parent.postMessage({ type: 'mythos:handshake' }, '*');
  }
});
async function calc() {
  const body = {lt, operation: document.getElementById('op').value,
    a: Number(document.getElementById('a').value), b: Number(document.getElementById('b').value)};
  const resp = await fetch('/calculate', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  document.getElementById('out').textContent = JSON.stringify(await resp.json(), null, 2);
}
</script>
</body></html>
"""


@app.get("/calculator", response_class=HTMLResponse)
async def calculator_page():
    return _CALCULATOR_HTML
