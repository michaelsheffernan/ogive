"""The in-chat viewer page (MCP Apps): one self-contained HTML file with three.js inlined.

Hosts render it in a sandboxed iframe whose default CSP blocks every network fetch, so nothing is
loaded from outside: the page, three.js (MIT, vendored) and the viewer code ship in one string.

Hosts may cache a ui:// resource by its URI, so the URI carries a hash of the page: a changed page
gets a new URI and a host can never pair a cached older page with a newer server.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from ogive import __version__

HERE = Path(__file__).parent

# The structuredContent kinds the page draws. app.js's MODES table must list the same ones, and
# every tool that opens the viewer sends one of them (tests/test_viewer.py checks all three).
MODES = ("design", "versions", "flight")


@lru_cache(maxsize=1)
def viewer_html() -> str:
    """The page, built once per process (about 700 KB, most of it three.js)."""
    template = (HERE / "app" / "viewer.html").read_text(encoding="utf-8")
    three = (HERE / "vendor" / "three.min.js").read_text(encoding="utf-8")
    code = (HERE / "app" / "app.js").read_text(encoding="utf-8")
    code = code.replace("__OGIVE_VERSION__", __version__)
    for name, script in (("three.min.js", three), ("app.js", code)):
        if "</script" in script.lower():
            raise ValueError(f"{name} contains '</script', which would end the inline script early")
    return template.replace("/*__THREE__*/", three, 1).replace("/*__APP__*/", code, 1)


@lru_cache(maxsize=1)
def viewer_uri() -> str:
    """ui://ogive/viewer-<first 12 hex of the page's sha256>.html"""
    digest = hashlib.sha256(viewer_html().encode("utf-8")).hexdigest()[:12]
    return f"ui://ogive/viewer-{digest}.html"
