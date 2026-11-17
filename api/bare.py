"""A function with no third-party imports at all.

/probe already removed Harvey from the question and still failed, which
left two possibilities that look identical from outside: the installed
dependencies are unusable (wrong Python version for the wheels, a bundle
over the size limit), or the project cannot run any Python function
whatever its contents.

This separates them. It imports only the standard library and uses the
plain handler interface, so it needs nothing installed and nothing
detected as an application framework.

  200  -> the runtime works and the Python version is in the response.
          The fault is in the installed dependencies.
  500  -> no function in this project can start regardless of its code.
          Nothing in this repository can fix it; the answer is in the
          project's own settings.

Serves no data and reads nothing, so it is safe on a public URL.
"""

import json
import platform
import sys
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = json.dumps({
            "ok": True,
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "note": "Stdlib only. If this works, the fault is in the "
                    "installed dependencies, not the runtime.",
        }, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
