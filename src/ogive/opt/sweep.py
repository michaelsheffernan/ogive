"""Optimizer v0: grid sweep over fin height and nose weight.

Every variant is a real .ork that OpenRocket can open. Each is flown in OpenRocket and judged
by the Lake 3 rules. There is no new physics here, only search and ranking:
- feasible = the judge finds no FAIL
- best = the lightest feasible design (ties go to the higher apogee)
- Pareto front = feasible designs that no other feasible design beats on both max Mach and
  worst ascent stability
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ogive.judge import FAIL, WARN, Report, judge_file
from ogive.ork_io import OrkDocument
from ogive.ork_io.components import Component

NOSE_WEIGHT_NAME = "Ogive nose weight"


@dataclass(frozen=True)
class NoseWeight:
    """Where a nose weight sits. ASSUMED: a lead slug just behind the tip, where a 30 mm cone is
    wide enough inside to hold it. Change it to match the real build."""

    top_offset: float = 0.10  # m from the nose tip to the front of the slug
    length: float = 0.02  # m
    radius: float = 0.007  # m


@dataclass(frozen=True)
class Variant:
    fin_height: float  # m (fin span)
    nose_mass: float  # kg; 0 = no nose weight

    @property
    def label(self) -> str:
        return f"fin{self.fin_height * 1000:g}mm_nose{self.nose_mass * 1000:g}g"


@dataclass(frozen=True)
class Row:
    """One judged variant, with the file it came from."""

    variant: Variant
    file: Path
    report: Report

    @property
    def passed(self) -> bool:
        return self.report.passed

    @property
    def meets_goals(self) -> bool:
        """Passed, and stability reached the goal (not only the minimum) everywhere."""
        return self.passed and not any(
            v.state == WARN for v in self.report.verdicts if v.rule.startswith("stability.")
        )

    @property
    def failures(self) -> list[str]:
        return [v.rule for v in self.report.verdicts if v.state == FAIL]

    def metric(self, key: str) -> float:
        return self.report.metrics[key]

    def measured(self, rule: str) -> float | None:
        values = [v.measured for v in self.report.verdicts if v.rule == rule]
        return values[0] if values else None


def frange(start: float, stop: float, step: float) -> list[float]:
    """start to stop inclusive, in steps: frange(30, 45, 5) -> [30, 35, 40, 45]."""
    if step <= 0 or stop < start:
        raise ValueError(f"bad range {start}:{stop}:{step}; need step > 0 and stop >= start")
    count = math.floor((stop - start) / step + 1e-9)
    return [round(start + i * step, 9) for i in range(count + 1)]


def parse_range(text: str) -> list[float]:
    """'30:45:1' -> [30, 31, ..., 45]; a single number '32' -> [32]."""
    try:
        parts = [float(p) for p in str(text).split(":")]
    except ValueError as error:
        raise ValueError(f"expected start:stop:step or one number, got {text!r}") from error
    if len(parts) == 1:
        return parts
    if len(parts) != 3:
        raise ValueError(f"expected start:stop:step or one number, got {text!r}")
    return frange(*parts)


def build_variant(
    base: str | Path, variant: Variant, out: str | Path, nose: NoseWeight | None = None
) -> Path:
    """Write `base` with this fin height and nose weight to `out`. Stored sims become outdated."""
    nose = nose or NoseWeight()
    doc = OrkDocument.load(base)
    _only(doc, "trapezoidfinset").set("height", variant.fin_height)
    if variant.nose_mass > 0:
        doc.add_mass_component(
            _only(doc, "nosecone").name,
            NOSE_WEIGHT_NAME,
            variant.nose_mass,
            nose.top_offset,
            nose.length,
            nose.radius,
        )
    doc.mark_simulations_outdated()
    return doc.save(out)


def sweep(
    base: str | Path,
    fin_heights: Sequence[float],
    nose_masses: Sequence[float],
    out_dir: str | Path,
    simulation: str | None = None,
    seed: int = 0,
    rules_path: str | Path | None = None,
    nose: NoseWeight | None = None,
    on_row: Callable[[Row], None] | None = None,
) -> list[Row]:
    """Build, fly and judge every (fin height, nose mass) pair. SI units."""
    rows = []
    for height in fin_heights:
        for mass in nose_masses:
            row = run_variant(
                base, Variant(height, mass), out_dir, simulation, seed, rules_path, nose
            )
            rows.append(row)
            if on_row:
                on_row(row)
    return rows


def run_variant(
    base: str | Path,
    variant: Variant,
    out_dir: str | Path,
    simulation: str | None = None,
    seed: int = 0,
    rules_path: str | Path | None = None,
    nose: NoseWeight | None = None,
) -> Row:
    """Build one variant as out_dir/variants/LABEL.ork, fly it and judge it."""
    path = build_variant(base, variant, Path(out_dir) / "variants" / f"{variant.label}.ork", nose)
    return Row(variant, path, judge_file(path, simulation, seed, rules_path))


def pareto_front(points: Sequence[tuple[float, float]]) -> list[int]:
    """Indices of the points that no other point beats on both axes (both maximised)."""
    front = []
    for i, (x, y) in enumerate(points):
        beaten = any(
            x2 >= x and y2 >= y and (x2 > x or y2 > y)
            for j, (x2, y2) in enumerate(points)
            if j != i
        )
        if not beaten:
            front.append(i)
    return front


def front_rows(rows: Sequence[Row]) -> list[Row]:
    """Feasible rows on the max Mach vs worst ascent stability front, by stability."""
    candidates = [r for r in rows if r.passed and r.measured("stability.ascent") is not None]
    points = [(r.measured("stability.ascent"), r.metric("max_mach")) for r in candidates]
    front = [candidates[i] for i in pareto_front(points)]
    return sorted(front, key=lambda r: r.measured("stability.ascent"))


def lightest(rows: Sequence[Row], goals: bool = False) -> Row | None:
    """The lightest passing row (or meeting every goal, with goals=True); ties: higher apogee."""
    pool = [r for r in rows if (r.meets_goals if goals else r.passed)]
    if not pool:
        return None
    return min(pool, key=lambda r: (r.metric("launch_mass"), -r.metric("apogee")))


def _only(doc: OrkDocument, kind: str) -> Component:
    found = [c for c in doc.components() if c.kind == kind]
    if len(found) != 1:
        raise ValueError(f"the optimizer needs exactly one {kind}; this design has {len(found)}")
    return found[0]
