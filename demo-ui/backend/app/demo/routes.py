"""/api/demo/* routes, HTTP basic auth, and static hosting of the React build."""

import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import settings
from .orchestrator import STEPS, demo
from .traffic import traffic

_basic = HTTPBasic()


def require_auth(creds: HTTPBasicCredentials = Depends(_basic)):
    ok = settings.DEMO_PASSWORD and secrets.compare_digest(creds.password.encode(), settings.DEMO_PASSWORD.encode())
    if not ok:
        raise HTTPException(401, "bad credentials", headers={"WWW-Authenticate": "Basic"})


router = APIRouter(prefix="/api/demo", dependencies=[Depends(require_auth)])


@router.get("/state")
def state():
    return demo.snapshot()


@router.post("/steps/{n}/run")
def run_step(n: int):
    if not 1 <= n <= len(STEPS):
        raise HTTPException(404, "no such step")
    try:
        demo.run_step(n)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"started": n}


@router.post("/reset-state")
def reset_state():
    try:
        demo.reset()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/traffic/stop")
def traffic_stop():
    traffic.stop()
    return {"ok": True}


@router.get("/stream")
async def stream(request: Request):
    """SSE: full snapshot every second plus the traffic history on connect."""
    async def gen():
        yield f"event: history\ndata: {json.dumps(list(traffic.points))}\n\n"
        while not await request.is_disconnected():
            yield f"event: snapshot\ndata: {json.dumps(demo.snapshot(), default=str)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
