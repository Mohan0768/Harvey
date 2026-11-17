"""A deliberately trivial function, to find out where the fault is.

The dashboard entry point returns a blank 500 on every path, and the
build succeeds, so the failure is at runtime and the platform's log is
the only place it is written down. This splits the question in two
without needing that log.

Reaching /probe and getting 200 means the Python runtime, the bundle and
Starlette are all fine, and the fault is in importing Harvey.

Getting 500 here too means nothing of ours is at fault: the runtime
cannot start any function in this project, and the answer is in the
platform's configuration rather than in this repository.

It imports nothing from harvey, touches no database and serves no data,
so it stays safe on a public URL whatever the answer turns out to be.
"""

import sys

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def probe(request):
    return JSONResponse({
        "ok": True,
        "python": sys.version.split()[0],
        "note": "The runtime works. Any 500 elsewhere is Harvey's import.",
    }, headers={"cache-control": "no-store"})


app = Starlette(routes=[Route("/{path:path}", probe)])
