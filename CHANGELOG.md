# Changelog

## 0.2.0 (2026-11-01)

**Added**: `render_design`, a to-scale side view of a design, drawn from OpenRocket's own model: nose profile,
tube, fins with their real sweep (each fin projected by its roll angle, as OpenRocket's side view does),
internal parts and the motor at their positions, CG at launch and burnout, CP, dimensions, masses and the
file's sha256. Returns a PNG for the chat and the SVG. Values you mark as guesses (by name, or "ASSUMED" in
the component's comment) are hatched and tagged. `edit_design` and `judge_design` now attach the picture too.
Also `ogive render` on the command line. New dependency: Pillow.

**Added**: `set_motor`. Swap the motor by designation; when several curves share a name it picks the one
whose integrated impulse matches the number in the name (within 5 %), keeps the ejection delay when the
motor allows it (else its longest), lengthens the mount if the case doesn't fit, and reloads the file in
OpenRocket to prove the curve resolved.

**Added**: `view_design`, an interactive 3D view in the chat (MCP Apps, `ui://ogive/viewer.html`). Bodies
are lathed from OpenRocket's own profiles, each fin is extruded from its real outline at its roll angle, and
internal parts and the motor sit at their positions. You can rotate, zoom and pan, toggle the CG (launch and
burnout) and CP markers, explode the internals onto lanes with names and masses, colour parts by mass, or make
the body see-through. ASSUMED parts are orange and labelled. The page is self-contained (three.js 0.160.0,
MIT, inlined), so it makes no network requests. Hosts without MCP Apps get the numbers and the side view.
`scripts/viewer_harness.py` runs the page in headless Chrome under a fake host and screenshots it.

**Added**: `view_versions`, a timeline of 2 to 12 versions of a design in the same viewer. A slider steps
through them; the previous version (or the first) is drawn as a ghost and the parts that changed are
violet. A table gives each version's judge result and what moved (apogee, max Mach and velocity, stability
at launch and burnout, CG, CP, masses, length, motor), with every field change listed as stored in the file.
Every version flies the same simulation, by name, and the same seed; a version without that simulation flies
its first and is named.

**Added**: `replay_flight`, a replay of one simulated flight in the same viewer. The design flies
OpenRocket's trajectory (east, north, altitude) with its attitude (elevation and azimuth of the axis) and
CG, over a ground grid with the launch rail and the path. It plays like a video: it starts on its own
(once, no loop), with a big play/pause button (or the space bar), the time as 0:01.2 / 1:34.8, a speed
menu (0.5x to 4x) and a timeline across the bottom whose labelled markers jump to each event: liftoff,
Mach 1, burnout, back below Mach 1, apogee, each deployment, landing. A big caption says what's happening
(Motor burning, SUPERSONIC, Coasting, Apogee, Under streamer, Under chute, Landed; SUPERSONIC flashes once)
and a heads-up box shows altitude, speed, Mach and acceleration in g. The camera follows the rocket from
behind; overview, fin, nose and a ground tracker are in a menu. OpenRocket stops simulating attitude at the
first deployment; from there the rocket is drawn faded and the caption says why. Roll comes from
integrating OpenRocket's roll rate; Mach 1 times are interpolated from its Mach series. "Replay of
simulation data, not footage" stays on screen. `run_simulation` gains the `theta`, `phi` and `roll_rate`
series, and flight events carry the component that caused them.

**Added**: a "Reference simulation" setting (`OGIVE_SIMULATION`). When a call doesn't name a simulation,
every tool that flies (`run_simulation`, `judge_design`, `compare_designs`, the optimizer, `view_versions`)
uses that one if the design has it, else the design's first. A simulation named in the call always wins.

**Fixed**: the viewer now fits the frame the host gives it (MCP Apps `containerDimensions`, fixed or
maximum height) and reports its own size, not the document's, so the frame can shrink as well as grow.
Tested at Claude Desktop's frame width (561 px, and 733 px in a wider window) and under a 400 px cap: the
player controls stay inside.

**Fixed**: a host could keep showing an older viewer page after an update ("unknown viewer mode
versions"). The viewer's address now carries a hash of the page (`ui://ogive/viewer-<hash>.html`), so every
build is a new resource. The page also never stops at an error: for anything it can't draw it shows the
tool's numbers and the side view, which is now attached to every viewer result. A test keeps the page's
modes and the server's in step.

**Changed**: OpenRocket's Java VM starts with the serial collector, a 64 MB starting heap and free heap handed
back. On a 3.5 GB laptop the defaults could fail to start ("paging file too small") with Claude Desktop open.
Peak memory for a judge run fell from 727 to 431 MB. Flight results are identical.

## 0.1.2 (2026-10-12)

**Added**: an icon. The Claude Desktop bundle declares it (`icon` plus 512, 256 and 64 px `icons` in the
manifest), and the MCP server sends it as its own icon and on every tool (the `icons` field from the MCP
2025-11-25 spec), embedded as a data URI so it works offline. It's the logo mark on a navy tile with transparent
corners, bold enough to read at 16 px on light and dark themes. `scripts/make_logo.py` draws it from the same
tangent-ogive geometry as the logo.

## 0.1.1 (2026-10-12)

Tested for real: installed in Claude Desktop from the `.mcpb`, and used `inspect_design`, `edit_design` and
`judge_design` from a Claude Desktop chat.

**Fixed**: relative paths. Claude Desktop starts the server in the bundle's own folder, so `rocket.ork` was looked
up there instead of in your designs folder. Relative paths now resolve inside `OGIVE_ROOT` when it's set, and still
can't escape it. Covered by four new tests, two of which run the tools from a different working folder the way
Claude Desktop does.

**Changed**: the Claude Desktop bundle sets `UV_HTTP_TIMEOUT=300`, so the first install doesn't time out
downloading `pydantic-core` on a slow connection.

**Docs**: a Troubleshooting section, a Tested section and a worked example in the README. The README no longer
describes one specific rocket.

**Added**: the logo, favicon and social-preview image (`assets/`, drawn from real tangent-ogive geometry by
`scripts/make_logo.py`). They first went public with the republished 0.1.0 tree.

## 0.1.0 (2026-10-11)

First version meant for other people. Windows, OpenRocket 24.12.

**MCP server** (`ogive-mcp`): 8 tools (`inspect_design`, `run_simulation`, `static_stability`, `judge_design`,
`edit_design`, `add_mass_component`, `compare_designs`, `optimize_fins_and_nose_weight`), the judge rules as a
resource, and an `improve_design` prompt. Every result carries provenance (backend, seed, file sha256). Tools read
only existing `.ork` files and write only new ones. `OGIVE_ROOT` confines paths. A project `.mcp.json` is included
for Claude Code, and `.\tasks.ps1 mcpb` builds a Claude Desktop bundle (uv runtime).

**Judge**: 14 rules in `rules.toml`. Stability at M0.3 and through the ascent, rod speed, fin flutter (Martin,
corrected constant), ground hit, recovery deployment warnings, thrust curve vs designation, stale stored results,
stored burn time, rocket-equation ceiling, mass consistency, mass-override traps, fineness, supersonic confidence.
Each verdict names its source and assumptions.

**Optimizer**: grid sweep of fin height and nose weight. Each variant is a real `.ork`, flown and judged. Reports
the lightest passing design, the lightest meeting every goal, and the Pareto front (CSV and SVG).

**Engine**: OpenRocket 24.12 driven headless through JPype. Results match the GUI's stability and stored sims.
Works around OpenRocket 24.12's ignored wind seed, so runs are reproducible.

**Files**: `.ork` read and write with a canonical round trip, field edits, added mass components, stored sims
marked outdated after edits.

**CLI**: `ogive tree | sim | judge | optimize | set`.

Docs: [229H255 case study](docs/case-study-229H255.md), [demo transcript](docs/demo-transcript.md),
[licensing](docs/LICENSING.md).
