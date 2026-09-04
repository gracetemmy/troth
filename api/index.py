"""Vercel entry point — the hosted read-mostly sandbox.

Read this before assuming the live demo proves anything about
persistence, because it does not, and that is a property of Vercel rather
than of Troth.

Sibyl Memory is a local SQLite file. Vercel runs serverless functions on
a read-only filesystem whose only writable directory, /tmp, is per
instance and disappears when that instance is recycled. So on Vercel:

  * reads work exactly as they do locally
  * writes work within one warm instance
  * writes DO NOT survive a cold start, and are not shared between
    concurrent instances

That makes the hosted build a browsable sandbox, not a demonstration of
cold-start recall. The load-bearing claim is verified locally, with the
commands in the README and in the demo video — never from this URL.

Every request is served from a copy of demo/demo-seed.db, so the sandbox
starts from the same seeded state each time an instance boots.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sys

# Must run before anything imports sibyl_memory_client, which grabs
# sqlite3 at import time. Vercel's runtime SQLite is missing the FTS5
# tokenizer Sibyl's search index builds on: reads worked, every write
# died with "error in tokenizer constructor". pysqlite3 carries its own
# modern SQLite, so aliasing it over the stdlib module fixes writes
# without touching a line of Troth or Sibyl.
try:
    import pysqlite3  # type: ignore

    sys.modules["sqlite3"] = pysqlite3
    sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
except ImportError:
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = ROOT / "demo" / "demo-seed.db"
LIVE = pathlib.Path("/tmp/troth-sandbox.db")


def _prepare_database() -> str:
    """Copy the seeded database into the only writable directory there is.

    If the seed is missing we still return the /tmp path rather than
    raising: Sibyl will create an empty database, and Troth's own
    "no policy in memory" refusal is a far more legible failure than a
    500 from an import-time exception.
    """
    if SEED.exists() and not LIVE.exists():
        try:
            # copyfile, not copy2: copy2 preserves the source's mode, and
            # files baked into a deployment bundle arrive read-only. That
            # produced a read-only copy in /tmp and every write failed with
            # a bare "SQLite error: OperationalError".
            shutil.copyfile(SEED, LIVE)
            LIVE.chmod(0o600)
        except OSError:
            pass
    return str(LIVE)


os.environ.setdefault("TROTH_UI", str(ROOT / "troth-v2-lethe-blue.html"))
os.environ["TROTH_SANDBOX"] = "1"

from troth.server import build_app  # noqa: E402  (after env setup)

app = build_app(_prepare_database())
