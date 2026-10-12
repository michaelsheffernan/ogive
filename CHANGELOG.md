# Changelog

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
