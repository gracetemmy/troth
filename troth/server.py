"""The read/write API the browser talks to.

Starlette and uvicorn ship with `sibyl-memory-cli[mcp]`, so this adds no
dependency a judge has to install. The UI is served from this same
process, so there is no CORS dance and no second thing to start.

This layer holds no state. Every endpoint reads or writes Sibyl through
`troth.memory` on each request. Stop the server, delete the database,
start it again, and every number on the dashboard is gone — which is the
point.
"""

from __future__ import annotations

import json
import pathlib
import traceback
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from . import ingest as I
from . import memory as M

UI_FILE = pathlib.Path(__file__).resolve().parent.parent / "troth-v2-lethe-blue.html"

# Which Sibyl tier each entity category is rendered as in the UI.
CATEGORY_TIER = {"client": "WARM", "promise": "WARM"}


def _fail(exc: Exception, status: int = 500) -> JSONResponse:
    """Surface the real reason. When Sibyl is missing, the dashboard must
    say so rather than quietly rendering zeros — an empty dashboard looks
    like a slow week, not a missing memory layer."""
    return JSONResponse(
        {"error": type(exc).__name__, "detail": str(exc)}, status_code=status
    )


def _node(record: dict) -> dict:
    """Flatten a Sibyl entity into the shape the graph renders."""
    body = record.get("body") or {}
    status = record.get("status")
    tier = CATEGORY_TIER.get(record.get("category"), "WARM")
    return {
        "id": record.get("name"),
        "category": record.get("category"),
        "tier": "FLAGGED" if status == M.FLAGGED else tier,
        "status": status,
        "name": body.get("text") or record.get("name"),
        "body": body,
        "updated": record.get("updated_at"),
    }


async def overview(request: Request) -> JSONResponse:
    """Everything the dashboard header and panels need, in one call."""
    m = request.app.state.memory
    try:
        clients = list(m.list_entities("client"))          # SIBYL WARM
        promises = list(m.list_entities("promise"))        # SIBYL WARM
        flagged = M.flagged_promises(m)                    # SIBYL WARM
        events = M.timeline(m, limit=25)                   # SIBYL COLD

        try:
            ceiling: Any = M.current_max_discount(m)       # SIBYL REFERENCE
        except M.MemoryUnavailable as exc:
            ceiling = None
            ceiling_error = str(exc)
        else:
            ceiling_error = None

        # Sibyl exposes no list_archived, so the count comes from the COLD
        # journal, which records every archive as it happens. That makes
        # the number a read of the timeline rather than a separate tally
        # Troth would have to keep in sync.
        archived = sum(
            1 for e in M.timeline(m, limit=500)             # SIBYL COLD
            if (e.get("extra") or {}).get("kind") == "client_archived"
        )

        return JSONResponse({
            "clients": len(clients),
            "promises": len(promises),
            "flagged": len(flagged),
            "archived": archived,
            "max_discount_pct": ceiling,
            "policy_error": ceiling_error,
            "flagged_items": [_node(p) for p in flagged],
            "nodes": [_node(r) for r in clients + promises],
            "events": [
                {
                    "ts": e.get("ts"),
                    "text": (e.get("acted") or [""])[0],
                    "kind": (e.get("extra") or {}).get("kind"),
                    "deal": (e.get("extra") or {}).get("deal"),
                }
                for e in events
            ],
        })
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        traceback.print_exc()
        return _fail(exc)


async def ingest(request: Request) -> JSONResponse:
    m = request.app.state.memory
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        return _fail(exc, 400)

    raw = (payload.get("transcript") or "").strip()
    client_id = (payload.get("client") or "").strip()
    if not raw:
        return JSONResponse({"error": "empty_transcript",
                             "detail": "Nothing to ingest."}, status_code=400)
    if not client_id:
        return JSONResponse({"error": "missing_client",
                             "detail": "Which client is this transcript for?"},
                            status_code=400)

    try:
        result = I.ingest_document(m, raw, client_id)
        return JSONResponse({
            "tally": result.tally,
            "routed": [r.__dict__ for r in result.routed],
        })
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return _fail(exc)


async def resolve(request: Request) -> JSONResponse:
    m = request.app.state.memory
    try:
        payload = await request.json()
        out = M.resolve_flag(
            m, payload["id"], payload["decision"], payload.get("note")
        )
        return JSONResponse(out)
    except KeyError as exc:
        return _fail(exc, 404)
    except ValueError as exc:
        return _fail(exc, 400)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return _fail(exc)


async def search(request: Request) -> JSONResponse:
    m = request.app.state.memory
    query = request.query_params.get("q", "").strip()
    if not query:
        return JSONResponse({"results": []})
    try:
        return JSONResponse(
            {"results": [_node(r) for r in M.search_active(m, query)]}
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)


async def ui(request: Request) -> Any:
    if not UI_FILE.exists():
        return JSONResponse(
            {"error": "ui_missing", "detail": f"{UI_FILE.name} not found"},
            status_code=404,
        )
    return FileResponse(UI_FILE)


def build_app(db_path: str | None = None) -> Starlette:
    app = Starlette(routes=[
        Route("/", ui),
        Route("/api/overview", overview),
        Route("/api/ingest", ingest, methods=["POST"]),
        Route("/api/resolve", resolve, methods=["POST"]),
        Route("/api/search", search),
    ])
    app.state.memory = M.connect(db_path)
    return app


def serve(host: str = "127.0.0.1", port: int = 8000, db_path: str | None = None) -> None:
    uvicorn.run(build_app(db_path), host=host, port=port, log_level="warning")
