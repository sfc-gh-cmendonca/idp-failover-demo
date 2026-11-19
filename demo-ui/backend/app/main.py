from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import config
from .config import settings
from .demo import settings as demo_settings
from .demo.orchestrator import demo
from .demo.routes import require_auth, router as demo_router
from .models import (
    ConfigResponse,
    StartTestResponse,
    TestConfigRequest,
    TestStatusResponse,
)
from .store import store

app = FastAPI(
    title="RDP Performance Test Harness API",
    description="HTTP API wrapping the Java JDBC load-test engine and Snowflake metrics.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(demo_router)


@app.on_event("startup")
def _start_demo_poller() -> None:
    demo.start_poller()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    return ConfigResponse(
        tables=config.TABLE_OPTIONS,
        query_mixes=config.QUERY_MIX_OPTIONS,
        exec_modes=config.EXEC_MODE_OPTIONS,
        ticker_biases=config.TICKER_BIAS_OPTIONS,
        result_limits=config.RESULT_LIMIT_OPTIONS,
        defaults={
            "table_id": "std_36b",
            "query_mix": "single",
            "exec_mode": "sync",
            "ticker_bias": "uniform",
            "num_connections": 100,
            "duration_seconds": 120,
            "result_limit": "0",
        },
    )


@app.post("/api/tests", response_model=StartTestResponse)
def start_test(req: TestConfigRequest) -> StartTestResponse:
    if config.resolve_table(req.table_id) is None:
        raise HTTPException(status_code=400, detail=f"Unknown table_id: {req.table_id}")
    if req.query_mix not in config.valid_ids(config.QUERY_MIX_OPTIONS):
        raise HTTPException(status_code=400, detail=f"Unknown query_mix: {req.query_mix}")
    if req.exec_mode not in config.valid_ids(config.EXEC_MODE_OPTIONS):
        raise HTTPException(status_code=400, detail=f"Unknown exec_mode: {req.exec_mode}")
    if req.ticker_bias not in config.valid_ids(config.TICKER_BIAS_OPTIONS):
        raise HTTPException(status_code=400, detail=f"Unknown ticker_bias: {req.ticker_bias}")
    if req.result_limit not in config.valid_ids(config.RESULT_LIMIT_OPTIONS):
        raise HTTPException(status_code=400, detail=f"Unknown result_limit: {req.result_limit}")

    run = store.start(req.model_dump())
    return StartTestResponse(test_id=run.test_id, status=run.status)


@app.get("/api/tests/{test_id}", response_model=TestStatusResponse)
def get_test(test_id: str) -> TestStatusResponse:
    run = store.get(test_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Test not found")
    return TestStatusResponse(**run.to_dict())


@app.post("/api/tests/{test_id}/stop")
def stop_test(test_id: str) -> dict:
    if store.get(test_id) is None:
        raise HTTPException(status_code=404, detail="Test not found")
    stopped = store.stop(test_id)
    return {"test_id": test_id, "stopped": stopped}


@app.get("/api/tests")
def list_tests() -> dict:
    return {
        "tests": [
            {
                "test_id": r.test_id,
                "status": r.status,
                "created_at": r.created_at,
                "table": r.table,
                "warehouse": r.warehouse,
            }
            for r in store.list()
        ]
    }


# React build (failover storyline UI). Registered last so /api/* wins; auth-gated.
@app.get("/{path:path}", include_in_schema=False, dependencies=[Depends(require_auth)])
def ui(path: str):
    root = demo_settings.STATIC_DIR.resolve()
    f = (root / path).resolve()
    if path and f.is_file() and root in f.parents:
        return FileResponse(f)
    index = root / "index.html"
    if not index.is_file():
        raise HTTPException(404, "frontend not built")
    return FileResponse(index)
