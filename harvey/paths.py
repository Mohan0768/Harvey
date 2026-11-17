"""Canonical filesystem locations for the Harvey checkout.

Harvey is repo-centric: config (harvey.yaml, .env), knowledge (prompts/,
skills/), and state (data/) all live next to the package. ``resolve()``
matters — when the package is reached through a symlink (some editable
installs), a bare ``Path(__file__).parent`` would point into
site-packages and Harvey would silently read/write the wrong files.

``DATA_DIR`` is the one location that moves. A container or a serverless
host mounts the checkout read-only and offers writable space somewhere
else, so HARVEY_DATA_DIR points the database and the logs at it. Unset,
it is ``data/`` beside the package, which is what a normal checkout
wants and what every existing install already has.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_override = (os.getenv("HARVEY_DATA_DIR") or "").strip()
DATA_DIR = Path(_override).expanduser().resolve() if _override \
    else PROJECT_ROOT / "data"
