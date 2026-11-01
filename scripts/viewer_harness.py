"""Prove the in-chat viewer runs: a fake MCP Apps host in headless Chrome, real data, screenshots.

    python scripts/viewer_harness.py DESIGN.ork -o OUT_DIR [--assumed NAME ...]   (view_design)
    python scripts/viewer_harness.py V1.ork V2.ork ... -o OUT_DIR                  (view_versions)
    python scripts/viewer_harness.py DESIGN.ork --replay [--simulation S] -o OUT  (replay_flight)
    python scripts/viewer_harness.py tool-result.json -o OUT_DIR                   (saved result)
    ... --unknown-mode   a kind the page doesn't know (checks the numbers + side view fallback)

The host acts like Claude Desktop: the iframe starts small, then takes the height the page
reports (ui/notifications/size-changed), capped or fixed by the shot's containerDimensions. Flight
shots use a 561 px wide frame, measured from Claude Desktop (733 in a wider window). The host
answers ui/initialize, sends the tool result, sets the controls for each shot, logs what the page
shows, and checks the player controls sit inside the frame. .ork files are read in a child
process, so the JVM is gone before Chrome starts (3.5 GB laptops can't hold both).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ogive.viewer.page import viewer_html  # noqa: E402

DESKTOP_WIDTH = 561  # Claude Desktop's app frame, measured from the team lead's screenshots
DESKTOP_WIDE = 733  # the same frame in a wider window
HEIGHTS = {"design": 560, "versions": 640, "flight": 520, "fallback": 900}  # page design heights


# Per viewer mode, name: state. toggles = checkboxes that should be on; view = camera button;
# index = timeline position (negative counts from the end); against = "Previous" or "First";
# camera = replay camera; time = replay time in s (None: leave it auto-playing); width = frame
# width; container = the host's containerDimensions.
def _flight(
    theme: str, camera: str, time: float | None, width: int = DESKTOP_WIDTH, **extra
) -> dict:
    return {"theme": theme, "toggles": [], "camera": camera, "time": time, "width": width, **extra}


SHOTS = {
    "flight": {
        "flight-autoplay": _flight("light", "Chase", None),
        "flight-supersonic": _flight("light", "Chase", 0.7),
        "flight-coasting": _flight("light", "Chase", 5.0),
        "flight-apogee": _flight("dark", "Chase", 15.1),
        "flight-under-chute": _flight("light", "Chase", 80.0),
        "flight-overview": _flight("light", "Overview", 15.0),
        "flight-ground-tracker": _flight("light", "Ground", 8.0),
        "flight-fin-dark": _flight("dark", "Fin", 3.0),
        "flight-capped-400": _flight("light", "Chase", 1.2, container={"maxHeight": 400}),
        "flight-wide-733": _flight("light", "Chase", 3.0, width=DESKTOP_WIDE),
        "flight-narrow-480": _flight("light", "Chase", 3.0, width=480),
    },
    "design": {
        "design-light": {"theme": "light", "toggles": ["CG / CP"], "view": "3/4"},
        "design-exploded-mass": {
            "theme": "light",
            "toggles": ["CG / CP", "Exploded internals", "Colour by mass"],
            "view": "3/4",
        },
        "design-dark-xray-side": {
            "theme": "dark",
            "toggles": ["CG / CP", "See-through body"],
            "view": "Side",
        },
        "design-tail": {"theme": "light", "toggles": ["CG / CP"], "view": "Tail"},
    },
    "fallback": {"fallback": {"theme": "light", "toggles": []}},
    "versions": {
        "versions-last": {
            "theme": "light",
            "toggles": ["CG / CP", "Ghost"],
            "view": "3/4",
            "index": -1,
            "against": "Previous",
        },
        "versions-middle-side": {
            "theme": "light",
            "toggles": ["CG / CP", "Ghost"],
            "view": "Side",
            "index": 2,
            "against": "Previous",
        },
        "versions-vs-first-dark": {
            "theme": "dark",
            "toggles": ["CG / CP", "Ghost", "See-through"],
            "view": "Side",
            "index": -1,
            "against": "First",
        },
        "versions-first": {
            "theme": "light",
            "toggles": ["CG / CP", "Ghost"],
            "view": "3/4",
            "index": 0,
        },
    },
}

HOST = """<!doctype html>
<html><head><meta charset="utf-8"><style>
html, body { margin: 0; background: #888; }
iframe { border: 0; width: %(width)dpx; height: 150px; display: block; }
</style></head><body>
<iframe id="view" sandbox="allow-scripts allow-same-origin"></iframe>
<script>
const VIEWER = %(viewer)s;
const RESULT = %(result)s;
const STATE = %(state)s;
const frame = document.getElementById("view");
const box = STATE.container || {};
if (box.height) frame.style.height = box.height + "px";
const log = (...a) => console.log("[host]", ...a);
const send = (m) => frame.contentWindow.postMessage(Object.assign({ jsonrpc: "2.0" }, m), "*");
window.addEventListener("message", (event) => {
  const m = event.data;
  if (!m || m.jsonrpc !== "2.0") return;
  if (m.method === "ui/initialize") {
    log("initialize", m.params.protocolVersion, JSON.stringify(m.params.appCapabilities));
    const hostContext = { theme: STATE.theme, displayMode: "inline" };
    if (STATE.container) hostContext.containerDimensions = STATE.container;
    send({ id: m.id, result: { protocolVersion: "2026-01-26",
      hostInfo: { name: "ogive-harness", version: "1" }, hostCapabilities: {}, hostContext } });
  } else if (m.method === "ui/notifications/initialized") {
    log("initialized");
    send({ method: "ui/notifications/tool-result", params: RESULT });
    setTimeout(applyState, 400);
  } else if (m.method === "ui/notifications/size-changed") {
    // Flexible height, like Claude Desktop: take the page's height, up to the cap.
    if (!box.height) frame.style.height = Math.min(m.params.height, box.maxHeight || 1e9) + "px";
    log("size-changed", m.params.width, m.params.height);
  } else {
    log("other", m.method || "response");
  }
});
function applyState() {
  const doc = frame.contentDocument;
  const message = doc.getElementById("message");
  if (message) { log("MESSAGE", message.textContent); return; }
  const click = (selector, text) => {
    for (const b of doc.querySelectorAll(selector)) if (b.textContent.trim() === text) b.click();
  };
  if (STATE.against) click("#side button", STATE.against);
  if (STATE.index !== undefined) {
    const ticks = doc.querySelectorAll(".ticks .tick");
    ticks[STATE.index < 0 ? ticks.length + STATE.index : STATE.index].click();
  }
  for (const label of doc.querySelectorAll("#controls label, #side label")) {
    const input = label.querySelector("input");
    if (input.checked !== STATE.toggles.includes(label.textContent.trim())) input.click();
  }
  if (STATE.view) click("#controls button, #side button", STATE.view);
  if (STATE.camera) {
    const select = doc.querySelector('select[aria-label="Camera"]');
    select.value = STATE.camera;
    select.dispatchEvent(new Event("change"));
  }
  if (STATE.time !== undefined && STATE.time !== null) {
    const play = doc.querySelector(".playbtn");
    if (play.getAttribute("aria-label") === "Pause") play.click();
    const tl = doc.querySelector(".tl");
    const rail = doc.querySelector(".tl .rail").getBoundingClientRect();
    const x = rail.left + (STATE.time / Number(tl.getAttribute("aria-valuemax"))) * rail.width;
    tl.dispatchEvent(new PointerEvent("pointerdown", { clientX: x, bubbles: true, pointerId: 1 }));
    tl.dispatchEvent(new PointerEvent("pointerup", { clientX: x, bubbles: true, pointerId: 1 }));
  }
  setTimeout(() => {
    const tags = [...doc.querySelectorAll(".tag")].filter((t) => t.style.display !== "none");
    log("tags", JSON.stringify(tags.map((t) => t.textContent)));
    const panel = doc.getElementById("info") || doc.getElementById("fallback");
    if (panel) log("info", JSON.stringify(panel.textContent.slice(0, 240)));
    const count = (sel) => doc.querySelectorAll(sel).length;
    log("images", count("img"), "canvas", count("canvas"));
    const text = (sel) => { const e = doc.querySelector(sel); return e ? e.textContent : null; };
    if (text(".fclock") !== null) {
      log("clock", JSON.stringify(text(".fclock")), "caption", JSON.stringify(text(".caption")),
        "play", doc.querySelector(".playbtn").getAttribute("aria-label"));
      log("hud", JSON.stringify(text(".fhud")));
    }
    const player = doc.querySelector(".player");
    if (player) {
      const bottom = player.getBoundingClientRect().bottom;
      const size = frame.clientWidth + "x" + frame.clientHeight;
      log(bottom <= frame.clientHeight + 0.5 ? "controls-inside" : "CONTROLS-CUT-OFF",
        "frame", size, "player bottom", bottom.toFixed(0));
    }
    const rows = [...doc.querySelectorAll("#side tr")].map((r) => r.textContent);
    if (rows.length) log("table", JSON.stringify(rows.slice(0, 6)));
    const mem = frame.contentWindow.performance.memory;
    if (mem) log("js-heap-mb", (mem.usedJSHeapSize / 1048576).toFixed(1));
    log("done");
  }, 1200);
}
frame.srcdoc = VIEWER;
</script></body></html>
"""


def find_chrome() -> str:
    for candidate in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ):
        if Path(candidate).exists():
            return candidate
    raise SystemExit("needs Google Chrome or Edge")


def tool_result(
    sources: list[Path], assumed: list[str], replay: bool, simulation: str | None
) -> dict:
    """The CallToolResult the tool returns: text record, side view PNG, structuredContent."""
    if len(sources) == 1 and sources[0].suffix.lower() == ".json":
        saved = json.loads(sources[0].read_text(encoding="utf-8"))
        if "structuredContent" in saved:
            return saved
        return {"content": [], "structuredContent": saved}  # bare viewer data (older runs)
    if replay:
        call = "tools.replay_flight(sys.argv[1], json.loads(sys.argv[2]))"
        args = [str(sources[0]), json.dumps(simulation)]
    elif len(sources) == 1:
        call = "tools.view_design(sys.argv[1], json.loads(sys.argv[2]))"
        args = [str(sources[0]), json.dumps(assumed)]
    else:
        call = "tools.view_versions(json.loads(sys.argv[1]))"
        args = [json.dumps([str(s) for s in sources])]
    code = (
        "import base64, json, sys\n"
        "from ogive.server import tools\n"
        f"data, drawing = {call}\n"
        "result = {'content': [\n"
        "    {'type': 'text', 'text': json.dumps(drawing.record, indent=2, default=str)},\n"
        "    {'type': 'image', 'mimeType': 'image/png',\n"
        "     'data': base64.b64encode(drawing.png).decode('ascii')}],\n"
        "  'structuredContent': data}\n"
        "print('OGIVE-RESULT ' + json.dumps(result))\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", code, *args], capture_output=True, text=True, cwd=REPO
    )
    if run.returncode != 0:
        raise SystemExit(f"Ogive failed (exit {run.returncode}): {run.stderr[-1500:]}")
    line = next(x for x in run.stdout.splitlines() if x.startswith("OGIVE-RESULT "))
    return json.loads(line.removeprefix("OGIVE-RESULT "))


def script_json(value: object) -> str:
    """JSON that is safe inside an inline <script> (no early end tag)."""
    return json.dumps(value).replace("</", "<" + chr(92) + "/")


def shoot(chrome: str, host: Path, out: Path, width: int, height: int) -> list[str]:
    """Screenshot the host page; returns the console lines (the host's log and any errors)."""
    profile = host.parent / "profile"  # a throwaway profile: no extensions, no user data
    try:
        run = subprocess.run(
            [
                chrome,
                "--headless=new",
                f"--user-data-dir={profile}",
                "--disable-extensions",
                "--no-first-run",
                "--disable-background-networking",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--enable-precise-memory-info",
                "--hide-scrollbars",
                "--enable-logging=stderr",
                "--v=1",
                "--virtual-time-budget=6000",
                f"--window-size={width},{height}",
                f"--screenshot={out}",
                host.as_uri(),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return ["Uncaught: Chrome timed out (low memory?)"]
    lines = []
    for line in run.stderr.splitlines():
        found = re.search(r'CONSOLE[:(]\d+\)?\] "(.*)", source', line)
        if found:
            lines.append(found.group(1))
        elif "Uncaught" in line:
            lines.append(line.strip())
    return lines


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console logs carry arrows
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sources", type=Path, nargs="+", help=".ork design(s) or a saved .json")
    parser.add_argument("-o", "--out", dest="out_dir", type=Path, required=True)
    parser.add_argument("--assumed", nargs="*", default=[])
    parser.add_argument("--replay", action="store_true", help="replay_flight of one .ork")
    parser.add_argument("--simulation", help="simulation for --replay")
    parser.add_argument("--shots", nargs="*", help="shot names (default: all for the mode)")
    parser.add_argument("--unknown-mode", action="store_true", help="test the page's fallback")
    args = parser.parse_args()

    result = tool_result(args.sources, args.assumed, args.replay, args.simulation)
    data = result["structuredContent"]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "tool-result.json").write_text(json.dumps(result), encoding="utf-8")
    if args.unknown_mode:
        result = {**result, "structuredContent": {**data, "kind": "from-the-future"}}
        data = {"kind": "fallback"}
    shots = SHOTS[data["kind"]]
    names = args.shots or list(shots)
    unknown = [n for n in names if n not in shots]
    if unknown:
        raise SystemExit(f"unknown shots {unknown}; {data['kind']} has {list(shots)}")
    chrome = find_chrome()
    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            state = shots[name]
            width = state.get("width", 960)
            container = state.get("container") or {}
            height = container.get("height") or container.get("maxHeight") or HEIGHTS[data["kind"]]
            host = Path(tmp) / f"{name}.html"
            host.write_text(
                HOST
                % {
                    "width": width,
                    "viewer": script_json(viewer_html()),
                    "result": script_json(result),
                    "state": script_json(state),
                },
                encoding="utf-8",
            )
            out = args.out_dir / f"{name}.png"
            log = shoot(chrome, host, out, width, height)
            ok = out.exists() and any("[host] done" in x for x in log)
            bad = ("MESSAGE", "Uncaught", "CONTROLS-CUT-OFF")
            ok = ok and not any(word in x for x in log for word in bad)
            failed = failed or not ok
            print(f"{'OK  ' if ok else 'FAIL'} {out}")
            for line in log:
                print("     " + line[:300])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
