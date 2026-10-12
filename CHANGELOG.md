# Changelog

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
