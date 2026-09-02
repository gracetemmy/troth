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

import datetime
import json
import pathlib
import traceback
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import commitments as C
from . import export as X
from . import ingest as I
from . import memory as M

def _find_ui() -> pathlib.Path | None:
    """The dashboard lives at the repo root as a design asset rather than
    inside the package. Look there first, then beside the package, so an
    editable install and a plain `python -m troth.cli` both find it."""
    here = pathlib.Path(__file__).resolve().parent
    for candidate in (here.parent / "troth-v2-lethe-blue.html",
                      here / "troth-v2-lethe-blue.html",
                      pathlib.Path.cwd() / "troth-v2-lethe-blue.html"):
        if candidate.exists():
            return candidate
    return None


UI_FILE = _find_ui()

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
            "commitments": len(M.list_commitments(m)),      # SIBYL WARM
            "overdue": sum(1 for r in X.commitment_rows(m)
                           if r["urgency"] == "OVERDUE"),
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


def _row(tier: str, primary: str, secondary: str = "",
         meta: str = "", status: str | None = None, rec: Any = None) -> dict:
    return {"tier": tier, "primary": primary, "secondary": secondary,
            "meta": meta, "status": status, "record": rec}


async def view(request: Request) -> JSONResponse:
    """Backs every sidebar item. Each one is a different question asked of
    the same five tiers, so they all read live rather than filtering a
    payload the browser already holds."""
    m = request.app.state.memory
    name = request.path_params["name"]

    try:
        if name == "clients":
            rows = [
                _row("WARM", c["name"],
                     " · ".join((c.get("body") or {}).get("facts", []))[:160]
                     or "no facts recorded yet",
                     (c.get("updated_at") or "")[:10], c.get("status"), _node(c))
                for c in m.list_entities("client")           # SIBYL WARM
            ]
            return JSONResponse({"title": "Clients",
                                 "note": "WARM entities. Archived clients are absent — "
                                         "Sibyl moves them out of this table entirely.",
                                 "rows": rows})

        if name == "promises":
            rows = [
                _row("FLAGGED" if p.get("status") == M.FLAGGED else "WARM",
                     (p.get("body") or {}).get("text", p["name"]),
                     (p.get("body") or {}).get("reason", ""),
                     (p.get("body") or {}).get("made_by") or "",
                     p.get("status"), _node(p))
                for p in m.list_entities("promise")          # SIBYL WARM
            ]
            return JSONResponse({"title": "Promises",
                                 "note": "Every commitment Troth has seen, with the "
                                         "trust status it carries.", "rows": rows})

        if name == "flags":
            rows = [
                _row("FLAGGED", (p.get("body") or {}).get("text", p["name"]),
                     (p.get("body") or {}).get("reason", ""),
                     (p.get("body") or {}).get("made_by") or "",
                     p.get("status"), _node(p))
                for p in M.flagged_promises(m)               # SIBYL WARM
            ]
            return JSONResponse({"title": "Flags",
                                 "note": "Awaiting a human decision. Troth will not "
                                         "repeat any of these as fact.", "rows": rows})

        if name == "commitments":
            rows = []
            for r in X.commitment_rows(m):
                rows.append(_row(
                    "FLAGGED" if r["urgency"] == "OVERDUE" else "WARM",
                    r["commitment"],
                    f'{r["client"]} · said "{r["said"] or "no date"}" · {r["detail"]}',
                    r["due"].isoformat() if r["due"] else "—", r["urgency"],
                ))
            return JSONResponse({"title": "Commitments",
                                 "note": "What Troth is owed and owes, by due date. "
                                         "Undated commitments are kept, not hidden — "
                                         "they are the ones that slip.", "rows": rows})

        if name == "deals":
            rows = [_row("HOT", d["client"], f"stage: {d.get('stage','unknown')}", "")
                    for d in M.list_deals(m)]                # SIBYL HOT
            return JSONResponse({"title": "Deals",
                                 "note": "HOT state — the live position on each deal.",
                                 "rows": rows})

        if name == "policies":
            rows = [_row("REFERENCE", p["key"], f"value: {p['value']}",
                         (p.get("updated") or "")[:10])
                    for p in M.list_policies(m)]             # SIBYL REFERENCE
            return JSONResponse({"title": "Policies",
                                 "note": "Standing rules. Every promise is checked "
                                         "against these at the moment it is made.",
                                 "rows": rows})

        if name in ("audit", "ingestion"):
            rows = [
                _row("COLD", (e.get("acted") or [""])[0],
                     (e.get("extra") or {}).get("kind", ""),
                     (e.get("ts") or "")[:19].replace("T", " "))
                for e in M.timeline(m, limit=200)            # SIBYL COLD
            ]
            return JSONResponse({"title": "Audit trail",
                                 "note": "The COLD journal, append-only. Every write "
                                         "Troth has made, in order.", "rows": rows})

        if name == "memory":
            rows = []
            for c in m.list_entities("client"):              # SIBYL WARM
                rows.append(_row("WARM", c["name"], "client", "", c.get("status"), _node(c)))
            for p in m.list_entities("promise"):             # SIBYL WARM
                rows.append(_row("FLAGGED" if p.get("status") == M.FLAGGED else "WARM",
                                 (p.get("body") or {}).get("text", p["name"]),
                                 "promise", "", p.get("status"), _node(p)))
            for d in M.list_deals(m):                        # SIBYL HOT
                rows.append(_row("HOT", d["client"], f"deal · {d.get('stage','')}", ""))
            for p in M.list_policies(m):                     # SIBYL REFERENCE
                rows.append(_row("REFERENCE", p["key"], f"value: {p['value']}", ""))
            return JSONResponse({"title": "Memory",
                                 "note": "Everything Troth holds, across all five "
                                         "Sibyl tiers.", "rows": rows})

        return JSONResponse({"error": "unknown_view", "detail": name}, status_code=404)

    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return _fail(exc)


def _qdate(request: Request, key: str):
    raw = request.query_params.get(key)
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw[:10])
    except ValueError:
        return None


async def export_xlsx(request: Request) -> Any:
    """Build the workbook on the spot from whatever is in memory now."""
    m = request.app.state.memory
    scope = request.query_params.get("scope", "commitments")
    try:
        wb = X.build_workbook(m, scope, _qdate(request, "from"), _qdate(request, "to"))
        stamp = datetime.date.today().isoformat()
        return Response(
            X.to_bytes(wb),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition":
                     f'attachment; filename="troth-{scope}-{stamp}.xlsx"'},
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return _fail(exc)


async def ui(request: Request) -> Any:
    if UI_FILE is None:
        return JSONResponse(
            {"error": "ui_missing",
             "detail": "troth-v2-lethe-blue.html not found. Run from the repo root."},
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
        Route("/api/view/{name}", view),
        Route("/api/export.xlsx", export_xlsx),
    ])
    app.state.memory = M.connect(db_path)
    return app


def serve(host: str = "127.0.0.1", port: int = 8000, db_path: str | None = None) -> None:
    uvicorn.run(build_app(db_path), host=host, port=port, log_level="warning")
