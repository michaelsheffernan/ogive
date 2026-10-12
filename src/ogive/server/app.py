"""Ogive MCP server (stdio). Clients bring the model; Ogive brings tools, judge and proof.

Run: `ogive-mcp`, or `python -m ogive.server`. No API key, no LLM inside Ogive.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ogive import __version__
from ogive.judge import load_rules
from ogive.judge.model import DEFAULT_RULES
from ogive.server import tools
from ogive.sim.openrocket import OpenRocketUnavailable

INSTRUCTIONS = """\
Ogive drives OpenRocket 24.12 headless: read and edit .ork designs, fly their simulations,
judge them against safety rules, compare versions and sweep fin size and nose weight.

Truth rules for anything you tell the user:
- Every number must come from a tool result. Name its backend and seed (see `provenance`).
- Edits always write a NEW file; the original is never touched. An edit proves nothing until
  judge_design or compare_designs has flown it.
- A design with any FAIL verdict is not a candidate. Report every ASSUMPTION the judge prints.
- Ogive is advisory. OpenRocket and RASAero II sign off on real flights.

Units are SI as stored in the file: m, kg, s, rad. Positions are from the nose tip.
Paths: relative paths are inside the user's designs folder (OGIVE_ROOT) when one is set.
The first OpenRocket call in a session takes about 10 s (Java start); later ones about 1 s.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITES_NEW_FILE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)

# Failures we expect (bad path, unknown component or sim, no OpenRocket): the client sees the
# message and can fix the call. Anything else is a crash: the SDK hides it and logs it.
ANTICIPATED = (ValueError, KeyError, OSError, OpenRocketUnavailable)


@contextmanager
def refusals() -> Iterator[None]:
    try:
        yield
    except ANTICIPATED as error:
        message = error.args[0] if isinstance(error, KeyError) and error.args else str(error)
        raise ToolError(message) from error


server = MCPServer(
    name="ogive",
    title="Ogive",
    description="Rocket design copilot over OpenRocket: judge, provenance, optimizer.",
    instructions=INSTRUCTIONS,
    version=__version__,
)


@server.tool(annotations=READ_ONLY)
def inspect_design(file: str) -> dict[str, Any]:
    """Read an .ork design: every component with its numeric fields (SI units, editable with
    edit_design), material, Ogive's mass estimate, the motors (with thrust-curve digest) and
    the stored simulations with their freshness. No simulation is run."""
    with refusals():
        return tools.inspect_design(file)


@server.tool(annotations=READ_ONLY)
def run_simulation(
    file: str,
    simulation: str | None = None,
    seed: int = 0,
    series: list[str] | None = None,
    max_points: int = 200,
) -> dict[str, Any]:
    """Fly one of the file's simulations in OpenRocket (default: the first). Same file and seed
    give the same flight. `series` picks time series to return (e.g. ["altitude", "velocity",
    "mach", "stability"]), downsampled to at most max_points."""
    with refusals():
        return tools.run_simulation(file, simulation, seed, series or (), max_points)


@server.tool(annotations=READ_ONLY)
def static_stability(file: str, mach: float = 0.3) -> dict[str, Any]:
    """CG, CP and stability in calibers exactly as OpenRocket's design view computes them, plus
    the burnout-CG stability (the one that matters at max velocity) and OpenRocket's masses."""
    with refusals():
        return tools.static_stability(file, mach)


@server.tool(annotations=READ_ONLY)
def judge_design(
    file: str, simulation: str | None = None, seed: int = 0, rules: str | None = None
) -> dict[str, Any]:
    """Fly a simulation and apply Ogive's judge: stability through the ascent, rod speed, fin
    flutter, ground hit, recovery warnings, motor curve vs designation, stale stored results,
    rocket-equation and mass sanity, confidence. Each verdict has a state (pass/warn/fail/
    pending), the measured value, the limit, its source and its assumptions. `rules` is an
    optional .toml overriding the default thresholds (read the ogive://rules/default resource)."""
    with refusals():
        return tools.judge_design(file, simulation, seed, rules)


@server.tool(annotations=WRITES_NEW_FILE)
def edit_design(
    file: str, changes: list[dict[str, Any]], output: str | None = None
) -> dict[str, Any]:
    """Change numeric fields and save the result as a NEW .ork (never overwrites). Each change is
    {"component": NAME, "field": TAG, "value": SI_NUMBER}; field names come from inspect_design.
    All changes apply or none do. Stored sims in the new file are marked outdated.
    Default output: DESIGN_FOLDER/ogive/STEM-001.ork (next free number)."""
    with refusals():
        return tools.edit_design(file, changes, output)


@server.tool(annotations=WRITES_NEW_FILE)
def add_mass_component(
    file: str,
    parent: str,
    name: str,
    mass_kg: float,
    top_offset_m: float,
    length_m: float = 0.02,
    radius_m: float = 0.007,
    output: str | None = None,
) -> dict[str, Any]:
    """Add a mass component (nose weight, ballast, avionics) inside the component `parent`, its
    front `top_offset_m` behind the parent's front. Saves a NEW .ork. Use it for nose weight:
    parent = the nose cone's name."""
    with refusals():
        return tools.add_mass_component(
            file, parent, name, mass_kg, top_offset_m, length_m, radius_m, output
        )


@server.tool(annotations=READ_ONLY)
def compare_designs(
    base: str,
    candidate: str,
    simulation: str | None = None,
    seed: int = 0,
    rules: str | None = None,
) -> dict[str, Any]:
    """The proof for a change: field-level diff of two designs, then both flown on the same
    simulation and seed and judged. Returns metric changes (apogee, max Mach, masses) and every
    verdict whose state changed."""
    with refusals():
        return tools.compare_designs(base, candidate, simulation, seed, rules)


@server.tool(annotations=WRITES_NEW_FILE)
async def optimize_fins_and_nose_weight(
    file: str,
    ctx: Context,
    fins_mm: str = "30:45:1",
    nose_g: str = "0:30:5",
    simulation: str | None = None,
    seed: int = 0,
    nose_at_m: float | None = None,
    out_dir: str | None = None,
    rules: str | None = None,
) -> dict[str, Any]:
    """Grid sweep: every fin height (mm, start:stop:step) x nose weight (g) becomes a real .ork,
    flown in OpenRocket and judged. Returns the lightest passing design, the lightest meeting
    every goal, the current design and the Pareto front (max Mach vs worst ascent stability),
    plus results.csv, pareto.svg and every variant file. About 0.8 s per variant; at most 200.
    nose_at_m: where the nose weight's front sits behind the tip (ASSUMED 0.10 m by default)."""
    with refusals():
        plan = tools.optimize_plan(file, fins_mm, nose_g, nose_at_m, out_dir, rules)
    rows = []
    total = len(plan.variants)
    for i, variant in enumerate(plan.variants):
        with refusals():
            row = await anyio.to_thread.run_sync(
                tools.optimize_variant, plan, variant, simulation, seed
            )
        rows.append(row)
        await ctx.report_progress(
            i + 1, total, f"{variant.label}: {'PASS' if row.passed else 'FAIL'}"
        )
    stability = load_rules(plan.rules_path)["stability"]
    with refusals():
        return tools.optimize_result(plan, rows, stability["min_cal"], stability["goal_cal"])


@server.resource(
    "ogive://rules/default",
    name="judge-rules",
    title="Ogive judge rules (default)",
    description="The default judge thresholds, with the reason for each. TOML.",
    mime_type="application/toml",
)
def default_rules() -> str:
    return DEFAULT_RULES.read_text(encoding="utf-8")


@server.prompt(title="Improve a design, with proof")
def improve_design(file: str, goal: str) -> str:
    """Work toward a design goal one proven change at a time."""
    return IMPROVE.format(file=file, goal=goal)


IMPROVE = """\
Goal: {goal}
Design: {file}

Work like this:
1. judge_design on the original. Note what passes, what fails, and which numbers are assumptions.
2. inspect_design to see the components and their editable fields (SI units).
3. Propose one change at a time, with a one-line reason.
4. edit_design or add_mass_component writes a new file. Never touch the original.
5. compare_designs (original vs new): show the field diff, the metric changes and every verdict
   whose state changed.
6. Repeat until the goal is met or the judge blocks it. A design with any FAIL is not a candidate.

Report: the winning file, its diff, a before/after table, and every ASSUMPTION the judge printed.
Every number must come from a tool result; name its backend and seed. Never invent a number.
Ogive is advisory: OpenRocket and RASAero II sign off on real flights.
"""


def main() -> None:
    server.run()  # stdio


if __name__ == "__main__":
    main()
