"""Troth's command line.

The demo runs from here, so the commands are the demo script. Every one
of them reads or writes Sibyl on the spot; none of them share state with
each other. `troth brief` in a brand new process is the cold-start recall
beat.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import commitments as C
from . import export as X
from . import ingest as I
from . import memory as M


def _out(text: str = "") -> None:
    """Windows consoles default to cp1252 and blow up on box-drawing
    characters, which is how the `sibyl` CLI itself crashes. Degrade to
    ASCII rather than dying mid-demo."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def cmd_seed(args, m) -> int:
    M.remember_pricing_rule(m, M.MAX_DISCOUNT_KEY, args.max_discount)
    _out(f"REFERENCE  discount ceiling = {args.max_discount:g}%")
    return 0


def cmd_ingest(args, m) -> int:
    raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    if not raw.strip():
        _out("Nothing to ingest: the transcript is empty.")
        return 1

    result = I.ingest_document(m, raw, args.client)
    _out(f"Extracted {len(result.routed)} lines -> " +
         ", ".join(f"{v} {k}" for k, v in sorted(result.tally.items())))
    _out()
    for r in result.routed:
        label = r.status or "-"
        _out(f"  {r.tier:9} {label:9} {r.text[:56]}")
        _out(f"  {'':19} -> {r.reason}")
    return 0


def cmd_brief(args, m) -> int:
    """The pre-call brief. This is the cold-start recall beat."""
    client = M.get_client(m, args.client)
    if client is None:
        if M.timeline(m):
            _out(f"'{args.client}' is not in active memory. It may have been "
                 f"archived — Troth will not suggest re-pitching it.")
            return 1
        _out("Memory is empty. Troth has nothing to brief you from.")
        return 1

    _out(f"CLIENT   {args.client}")
    for fact in (client.get("body") or {}).get("facts", []):
        _out(f"         - {fact}")

    deal = M.get_deal(m, args.client)
    if deal:
        _out(f"DEAL     stage: {deal.get('stage')}")

    try:
        _out(f"POLICY   max discount {M.current_max_discount(m):g}%")
    except M.MemoryUnavailable as exc:
        _out(f"POLICY   UNAVAILABLE - {exc}")

    owed = [r for r in X.commitment_rows(m) if r["client"] == args.client]
    if owed:
        _out()
        _out(f"{len(owed)} open commitment(s):")
        for r in owed:
            mark = "!" if r["urgency"] in ("OVERDUE", "DUE") else " "
            _out(f"  {mark} {r['detail']:24} {r['commitment'][:60]}")

    flagged = [p for p in M.flagged_promises(m)
               if (p.get("body") or {}).get("deal") == args.client]
    if flagged:
        _out()
        _out(f"{len(flagged)} promise(s) NOT confirmed. Do not repeat these:")
        for p in flagged:
            body = p.get("body") or {}
            _out(f"  [{p['name']}]")
            _out(f"    \"{body.get('text','')}\"")
            _out(f"    {body.get('reason','')}")
    else:
        _out()
        _out("No unverified promises outstanding.")
    return 0


def cmd_flags(args, m) -> int:
    flagged = M.flagged_promises(m)
    if not flagged:
        _out("Nothing awaiting review.")
        return 0
    for p in flagged:
        body = p.get("body") or {}
        _out(f"[{p['name']}] {body.get('text','')}")
        _out(f"    {body.get('reason','')}")
    return 0


def cmd_resolve(args, m) -> int:
    try:
        out = M.resolve_flag(m, args.id, args.decision, args.note)
    except KeyError:
        _out(f"No promise {args.id!r} in memory.")
        return 1
    _out(f"{args.id} -> {out['status']}")
    return 0


def cmd_archive(args, m) -> int:
    try:
        M.archive_client(m, args.client, args.reason)
    except KeyError:
        _out(f"No client {args.client!r} in memory.")
        return 1
    _out(f"{args.client} archived: {args.reason}")
    _out("Troth will no longer suggest outreach for this client.")
    return 0


def cmd_due(args, m) -> int:
    rows = X.commitment_rows(m)
    if not rows:
        _out("Nothing outstanding.")
        return 0
    for r in rows:
        mark = "!" if r["urgency"] in ("OVERDUE", "DUE") else " "
        _out(f"{mark} {str(r['due'] or '—'):11} {r['urgency']:8} "
             f"{r['client']:14} {r['commitment'][:52]}")
        _out(f"  {'':11} {r['detail']}")
    return 0


def cmd_export(args, m) -> int:
    from datetime import date as _date
    parse = lambda v: _date.fromisoformat(v) if v else None
    wb = X.build_workbook(m, args.scope, parse(args.date_from), parse(args.date_to))
    wb.save(args.out)
    _out(f"Wrote {args.out}  (scope: {args.scope})")
    return 0


def cmd_serve(args, m) -> int:
    from . import server
    _out(f"Troth on http://{args.host}:{args.port}  (ctrl-c to stop)")
    server.serve(args.host, args.port, args.db)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="troth", description=__doc__)
    p.add_argument("--db", default=None, help="path to the Sibyl database")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("seed", help="write the standing pricing policy")
    s.add_argument("--max-discount", type=float, default=15.0)
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("ingest", help="route a transcript into memory")
    s.add_argument("file", help="transcript file, or - for stdin")
    s.add_argument("--client", required=True)
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("brief", help="pre-call brief, read entirely from memory")
    s.add_argument("client")
    s.set_defaults(func=cmd_brief)

    s = sub.add_parser("flags", help="promises awaiting a human")
    s.set_defaults(func=cmd_flags)

    s = sub.add_parser("resolve", help="approve or retract a flagged promise")
    s.add_argument("id")
    s.add_argument("decision", choices=["approve", "retract"])
    s.add_argument("--note")
    s.set_defaults(func=cmd_resolve)

    s = sub.add_parser("archive", help="retire a client")
    s.add_argument("client")
    s.add_argument("--reason", required=True)
    s.set_defaults(func=cmd_archive)

    s = sub.add_parser("due", help="open commitments, soonest first")
    s.set_defaults(func=cmd_due)

    s = sub.add_parser("export", help="write an .xlsx of what is owed")
    s.add_argument("--out", default="troth-export.xlsx")
    s.add_argument("--scope", choices=["commitments", "all"], default="commitments")
    s.add_argument("--from", dest="date_from", help="earliest due date (YYYY-MM-DD)")
    s.add_argument("--to", dest="date_to", help="latest due date (YYYY-MM-DD)")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("serve", help="run the dashboard")
    # Hosts that give you a real machine hand you the port and expect you
    # to bind every interface. Locally the defaults stay loopback.
    s.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    try:
        memory = M.connect(args.db)
    except M.MemoryUnavailable as exc:
        _out(str(exc))
        return 2

    try:
        return args.func(args, memory)
    except M.MemoryUnavailable as exc:
        _out(f"Troth cannot answer: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
