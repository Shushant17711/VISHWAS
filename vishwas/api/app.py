"""VISHWAS control API.

Two things are served here:

* **Missions** - a swarm flown live, streamed tick by tick.  This is what the
  dashboard watches: suspicion climbing, the vote clearing quorum, the traitor
  leaving the formation.
* **Results** - the evaluation bundles written by ``scripts/run_evaluation.py``.
  The dashboard never computes a headline number; it reads the same JSON that
  the report does, so a figure on screen and a figure in the paper cannot drift
  apart.

Conventions: ``/api/v1`` prefix, plural resources, ``{"data": ...}`` on success
and ``{"error": {"code", "message"}}`` on failure, with the HTTP status
carrying the meaning.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ..config import SimConfig
from ..sim.attacks import SCENARIOS
from .runner import MissionError, MissionRegistry, MissionSpec

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "results"

registry = MissionRegistry()


# ----------------------------------------------------------------------
# request models
# ----------------------------------------------------------------------
class MissionRequest(BaseModel):
    scenario: str = Field(default="position_teleport")
    n_compromised: int = Field(default=1, ge=0, le=8)
    n_drones: int = Field(default=9, ge=4, le=25)
    max_ticks: int = Field(default=1200, ge=50, le=5000)
    seed: int = Field(default=7, ge=0)
    attack_start: int = Field(default=60, ge=0)
    enable_consensus: bool = True
    enable_trust_priors: bool = True
    #: Off reproduces the pre-ClaimCheck majority vote, which is what the
    #: side-by-side demo needs: same seed, same attack, accusations counted
    #: instead of verified.
    enable_claim_verifier: bool = True
    speed: float = Field(default=4.0, gt=0, le=50)
    certified_anchors: list[int] = Field(default_factory=list)

    def to_spec(self) -> MissionSpec:
        return MissionSpec(**self.model_dump())


class MissionPatch(BaseModel):
    paused: bool | None = None
    speed: float | None = Field(default=None, gt=0, le=50)


# ----------------------------------------------------------------------
def error(code: str, message: str, status: int, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


def _field_error(e: dict[str, Any]) -> dict[str, str]:
    """One pydantic error, named the way the caller wrote the field.

    Pydantic reports ``("body", "n_drones")``; the caller sent ``n_drones``.
    The envelope prefix is ours, not theirs, so it is dropped.
    """
    loc = [str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path")]
    return {"field": ".".join(loc) or "body", "message": str(e.get("msg", ""))}


def create_app() -> FastAPI:
    app = FastAPI(
        title="VISHWAS",
        version="1.0.0",
        description="Trust-weighted Byzantine consensus for autonomous drone swarms.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # The dashboard is a handful of static files served straight off disk
    # (no build step, no cache-busted filenames) so that editing them and
    # reloading the browser is the entire dev loop. Browsers cache static
    # assets aggressively by default, which - combined with `--reload` not
    # reliably picking up source edits either (see CONTINUE_FROM_HERE.md) -
    # has twice now made a fixed bug look unfixed because the *served* copy
    # was stale, not the code. Disabling caching on `/app/*` trades a few
    # extra bytes per request for "what's on screen is what's on disk".
    @app.middleware("http")
    async def _no_cache_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/app/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response

    @app.exception_handler(MissionError)
    async def _mission_error(_: Request, exc: MissionError) -> JSONResponse:
        return error(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def _validation_error(
        _: Request, exc: ValidationError | RequestValidationError
    ) -> JSONResponse:
        return error(
            "validation_error",
            "Request validation failed.",
            422,
            [_field_error(e) for e in exc.errors()],
        )

    # ------------------------------------------------------------------
    @app.get("/api/v1/health")
    async def health() -> dict[str, Any]:
        return {
            "data": {
                "status": "ok",
                "missions": len(registry.list()),
                "results": RESULTS_DIR.exists(),
            }
        }

    @app.get("/api/v1/scenarios")
    async def scenarios() -> dict[str, Any]:
        return {
            "data": [
                {
                    "id": s["id"],
                    "key": s["key"],
                    "title": s["title"],
                    "tests": s["tests"],
                    "n_compromised": s["n_compromised"],
                }
                for s in SCENARIOS
            ],
            "meta": {"defaults": SimConfig().to_dict()},
        }

    # ------------------------------------------------------------------
    @app.post("/api/v1/missions", status_code=201)
    async def create_mission(req: MissionRequest) -> JSONResponse:
        mission = registry.create(req.to_spec())
        return JSONResponse(
            {"data": mission.snapshot(), "meta": {"mission": mission.meta}},
            status_code=201,
            headers={"Location": f"/api/v1/missions/{mission.id}"},
        )

    @app.get("/api/v1/missions")
    async def list_missions() -> dict[str, Any]:
        rows = [m.snapshot() for m in registry.list()]
        return {"data": rows, "meta": {"total": len(rows), "capacity": registry.capacity}}

    @app.get("/api/v1/missions/{mission_id}")
    async def get_mission(mission_id: str) -> dict[str, Any]:
        mission = registry.get(mission_id)
        return {"data": mission.snapshot(), "meta": {"mission": mission.meta}}

    @app.patch("/api/v1/missions/{mission_id}")
    async def patch_mission(mission_id: str, patch: MissionPatch) -> dict[str, Any]:
        mission = registry.get(mission_id)
        if patch.speed is not None:
            mission.set_speed(patch.speed)
        if patch.paused is True:
            mission.pause()
        elif patch.paused is False:
            mission.resume()
        return {"data": mission.snapshot()}

    @app.delete("/api/v1/missions/{mission_id}", status_code=204, response_class=Response)
    async def delete_mission(mission_id: str) -> Response:
        registry.delete(mission_id)
        return Response(status_code=204)

    @app.get("/api/v1/missions/{mission_id}/frames")
    async def mission_frames(
        mission_id: str,
        after: int = Query(default=0, ge=0, description="Frame cursor from the last page."),
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> dict[str, Any]:
        return registry.get(mission_id).page(after=after, limit=limit)

    @app.websocket("/api/v1/missions/{mission_id}/stream")
    async def mission_stream(ws: WebSocket, mission_id: str) -> None:
        await ws.accept()
        try:
            mission = registry.get(mission_id)
        except MissionError as exc:
            await ws.send_json({"kind": "error", "error": {"code": exc.code, "message": exc.message}})
            await ws.close()
            return
        await ws.send_json({"kind": "meta", "data": mission.meta, "mission": mission.snapshot()})
        cursor = 0
        try:
            while True:
                page = mission.page(after=cursor, limit=400)
                cursor = page["meta"]["cursor"]
                if page["data"] or page["meta"]["events"]:
                    await ws.send_json(
                        {
                            "kind": "frames",
                            "data": page["data"],
                            "events": page["meta"]["events"],
                            "mission": mission.snapshot(),
                        }
                    )
                if not page["meta"]["has_next"]:
                    await ws.send_json({"kind": "end", "mission": mission.snapshot()})
                    break
                await asyncio.sleep(0.08)
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            try:
                await ws.close()
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    @app.get("/api/v1/results", response_model=None)
    async def results_index() -> JSONResponse | dict[str, Any]:
        index = RESULTS_DIR / "index.json"
        if not index.exists():
            return error(
                "no_results",
                "No evaluation bundles yet. Run: python scripts/run_evaluation.py --quick",
                404,
            )
        return {"data": json.loads(index.read_text(encoding="utf-8"))}

    @app.get("/api/v1/results/{name}", response_model=None)
    async def result_bundle(name: str) -> JSONResponse | dict[str, Any]:
        path = RESULTS_DIR / f"{name}.json"
        if not path.is_file() or path.parent != RESULTS_DIR:
            return error("not_found", f"No result bundle named {name!r}.", 404)
        return {"data": json.loads(path.read_text(encoding="utf-8"))}

    # ------------------------------------------------------------------
    if UI_DIR.is_dir():
        app.mount("/app", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(str(UI_DIR / "index.html"))

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        registry.shutdown()

    return app


app = create_app()
