"""Ogive's tools as plain functions: JSON-ready dicts out, no MCP import.

The MCP server (app.py) wraps these. Every result that holds numbers carries `provenance`:
the backend that produced them, the seed, and the exact file they came from (sha256).

Files: tools read only existing .ork files and write only NEW .ork files (never overwrite).
Set OGIVE_ROOT to confine every path to one folder.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ogive import __version__
from ogive.judge import Report, judge_file
from ogive.judge.model import DEFAULT_RULES
from ogive.opt import output
from ogive.opt.sweep import (
    NoseWeight,
    Row,
    Variant,
    front_rows,
    lightest,
    parse_range,
    run_variant,
)
from ogive.ork_io import OrkDocument
from ogive.ork_io.components import Component, parse_value
from ogive.ork_io.mass import BACKEND as MASS_BACKEND
from ogive.ork_io.mass import CONTAINER_KINDS, component_mass, dry_mass
from ogive.sim import openrocket

ADVISORY = "Advisory only: OpenRocket and RASAero II sign off on real flights."
ROOT_ENV = "OGIVE_ROOT"
MAX_VARIANTS = 200  # about 0.8 s each in OpenRocket 24.12
SWEEP_OUTPUTS = {"results.csv", "pareto.svg", "variants"}  # what a sweep writes, and may redo
ORK_IO = f"Ogive ork_io {__version__}"

# One OpenRocket at a time: tool calls arrive on worker threads, the JVM is shared.
_openrocket = threading.Lock()


class ToolError(ValueError):
    """A request Ogive refuses, with a message the client can act on."""


# --- paths and provenance ---------------------------------------------------------------


def design_path(file: str) -> Path:
    """An existing .ork file, inside OGIVE_ROOT when that is set."""
    path = _inside_root(Path(file).expanduser())
    if path.suffix.lower() != ".ork":
        raise ToolError(f"{path.name} is not an .ork file")
    if not path.is_file():
        raise ToolError(f"no such file: {path}")
    return path


def new_output_path(output: str | None, source: Path) -> Path:
    """Where to write a new design. Default: SOURCE_DIR/ogive/STEM-001.ork, first free number."""
    if output:
        path = _inside_root(Path(output).expanduser())
    else:
        folder = source.parent / "ogive"
        n = 1
        while (folder / f"{source.stem}-{n:03d}.ork").exists():
            n += 1
        path = _inside_root(folder / f"{source.stem}-{n:03d}.ork")
    if path.suffix.lower() != ".ork":
        raise ToolError(f"output {path.name} must end in .ork")
    if path.exists():
        raise ToolError(f"refusing to overwrite {path}; pick a new output path")
    return path


def provenance(path: Path, backend: str, **extra: Any) -> dict[str, Any]:
    """Who produced the numbers, from which exact bytes."""
    record = {
        "file": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "backend": backend,
        "ogive": __version__,
    }
    record.update({k: v for k, v in extra.items() if v is not None})
    return record


def _inside_root(path: Path) -> Path:
    path = path.resolve()
    root = os.environ.get(ROOT_ENV)
    if root and not path.is_relative_to(Path(root).resolve()):
        raise ToolError(f"{path} is outside {ROOT_ENV} ({Path(root).resolve()})")
    return path


# --- reading ------------------------------------------------------------------------------


def inspect_design(file: str) -> dict[str, Any]:
    """Components with every numeric field (SI), Ogive's mass estimate, motors, stored sims."""
    path = design_path(file)
    doc = OrkDocument.load(path)
    components = doc.components()
    summary = dry_mass(components)
    return {
        "components": [_component_record(c) for c in components],
        "motors": [asdict(m) for m in doc.motors()],
        "dry_mass_kg": summary.total,
        "dry_mass_source": MASS_BACKEND,
        "mass_not_modelled": list(summary.unmodelled),
        "stored_simulations": [
            {
                "name": s.name,
                "status": s.status,
                "freshness": s.freshness,
                "conditions": s.conditions,
                "stored_summary": s.flight_data,
            }
            for s in doc.simulations()
        ],
        "notes": [
            "Units are SI as stored in the file: m, kg, kg/m^3, rad.",
            "Stored summaries are hints, never results: re-run with run_simulation.",
            f"Masses are {MASS_BACKEND} estimates; OpenRocket's own come from static_stability.",
        ],
        "provenance": provenance(path, ORK_IO),
    }


def _component_record(c: Component) -> dict[str, Any]:
    fields, auto = {}, []
    for child in c.element:
        if len(child) or child.tag == "id" or not (child.text or "").strip():
            continue
        try:
            value = parse_value(child.text)
        except ValueError:
            continue  # text fields (shape, material name, ...) are not editable numbers
        fields[child.tag] = value.value
        if value.auto:
            auto.append(child.tag)
    record: dict[str, Any] = {
        "name": c.name,
        "kind": c.kind,
        "depth": c.depth,
        "parent": c.parent.name if c.parent else None,
        "fields": fields,
        "auto_fields": auto,
    }
    material = c.material()
    if material:
        record["material"] = asdict(material)
    if c.kind not in CONTAINER_KINDS:
        record["mass_kg"] = component_mass(c)
    return record


def run_simulation(
    file: str,
    simulation: str | None = None,
    seed: int = 0,
    series: Sequence[str] = (),
    max_points: int = 200,
) -> dict[str, Any]:
    """Fly one of the file's simulations in OpenRocket. Same file + same seed = same flight."""
    path = design_path(file)
    unknown = set(series) - set(openrocket.SERIES)
    if unknown:
        raise ToolError(f"unknown series {sorted(unknown)}; known: {sorted(openrocket.SERIES)}")
    with _openrocket:
        result = openrocket.simulate(path, simulation, seed)
    record: dict[str, Any] = {
        "simulation": result.simulation,
        "summary": asdict(result.summary),
        "conditions": asdict(result.conditions),
        "events": [asdict(e) for e in result.events if e.type != "Warning"],
        "warnings": result.warnings,
        "notes": ["ground_hit_velocity is TOTAL speed in OpenRocket 24.12, not vertical."],
        "provenance": provenance(path, result.backend, seed=result.seed),
    }
    if series:
        keys = ["time", *[k for k in series if k != "time"]]
        record["series"] = _downsample({k: result.series[k] for k in keys}, max_points)
    return record


def _downsample(series: dict[str, list[float]], max_points: int) -> dict[str, list[float]]:
    """Every n-th sample so at most max_points remain; the last sample is always kept."""
    length = len(series["time"])
    if max_points < 2:
        raise ToolError("max_points must be at least 2")
    step = max(1, -(-length // max_points))
    picks = list(range(0, length, step))
    if picks[-1] != length - 1:
        picks[-1] = length - 1
    return {k: [v[i] for i in picks] for k, v in series.items()}


def static_stability(file: str, mach: float = 0.3) -> dict[str, Any]:
    """CG, CP and stability as OpenRocket's design view shows them, plus burnout stability."""
    path = design_path(file)
    with _openrocket:
        a = openrocket.static_analysis(path, mach)
    return {
        "mach": a.mach,
        "cg_m": a.cg_x,
        "cg_burnout_m": a.cg_burnout_x,
        "cp_m": a.cp_x,
        "stability_cal": a.calibers,
        "stability_burnout_cal": a.burnout_calibers,
        "stability_percent_of_length": a.percent_of_length,
        "length_m": a.length,
        "max_diameter_m": a.max_diameter,
        "launch_mass_kg": a.mass_with_motors,
        "dry_mass_kg": a.mass_without_motors,
        "components": [asdict(c) for c in a.components],
        "warnings": a.warnings,
        "notes": ["Positions are from the nose tip. CP is Barrowman, worst case over roll, AOA 0."],
        "provenance": provenance(path, f"{a.backend} Barrowman + MassCalculator"),
    }


def judge_design(
    file: str, simulation: str | None = None, seed: int = 0, rules: str | None = None
) -> dict[str, Any]:
    """Fly a simulation and apply the judge rules. passed = no FAIL."""
    path = design_path(file)
    rules_path = design_rules_path(rules)
    with _openrocket:
        report = judge_file(path, simulation, seed, rules_path)
    return report_record(report, path, rules_path)


def design_rules_path(rules: str | None) -> Path | None:
    if rules is None:
        return None
    path = _inside_root(Path(rules).expanduser())
    if path.suffix.lower() != ".toml" or not path.is_file():
        raise ToolError(f"rules must be an existing .toml file: {path}")
    return path


def report_record(report: Report, path: Path, rules_path: Path | None = None) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "counts": {s: report.count(s) for s in ("pass", "warn", "fail", "pending")},
        "simulation": report.simulation,
        "metrics": report.metrics,
        "verdicts": [asdict(v) for v in report.verdicts],
        "advisory": ADVISORY,
        "provenance": provenance(
            path,
            report.backend,
            seed=report.seed,
            rules=str(rules_path) if rules_path else f"default ({DEFAULT_RULES.name})",
        ),
    }


# --- writing ------------------------------------------------------------------------------


def edit_design(file: str, changes: Sequence[dict], output: str | None = None) -> dict[str, Any]:
    """Apply numeric field changes (SI units) and save as a NEW file. All or nothing.

    changes: [{"component": "Trapezoidal Fin Set", "field": "height", "value": 0.034}, ...]
    """
    path = design_path(file)
    if not changes:
        raise ToolError("no changes given")
    doc = OrkDocument.load(path)
    applied = []
    for change in changes:
        missing = {"component", "field", "value"} - set(change)
        if missing:
            raise ToolError(f"change {change} is missing {sorted(missing)}")
        try:
            component = doc.find_component(str(change["component"]))
            old = component.get(str(change["field"]))
            component.set(str(change["field"]), float(change["value"]))
        except (KeyError, ValueError) as error:
            raise ToolError(error.args[0] if error.args else str(error)) from error
        applied.append(
            {
                "component": component.name,
                "field": str(change["field"]),
                "before": old.value,
                "before_was_auto": old.auto,
                "after": float(change["value"]),
            }
        )
    return _save_new(doc, path, output, {"changes": applied})


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
    """Add a mass component (nose weight, ballast, avionics) inside `parent`; save as a NEW file."""
    path = design_path(file)
    doc = OrkDocument.load(path)
    try:
        added = doc.add_mass_component(parent, name, mass_kg, top_offset_m, length_m, radius_m)
    except (KeyError, ValueError) as error:
        raise ToolError(error.args[0] if error.args else str(error)) from error
    centre = top_offset_m + length_m / 2
    return _save_new(
        doc,
        path,
        output,
        {
            "added": _component_record(added),
            "centre_of_mass_from_parent_front_m": centre,
        },
    )


def _save_new(doc: OrkDocument, source: Path, output: str | None, record: dict) -> dict:
    target = new_output_path(output, source)
    outdated = doc.mark_simulations_outdated()
    doc.save(target)
    record.update(
        {
            "output": str(target),
            "simulations_marked_outdated": outdated,
            "source": provenance(source, ORK_IO),
            "provenance": provenance(target, ORK_IO),
            "next": "judge_design or compare_designs on the new file; nothing is proven yet.",
        }
    )
    return record


# --- comparing ----------------------------------------------------------------------------


def compare_designs(
    base: str,
    candidate: str,
    simulation: str | None = None,
    seed: int = 0,
    rules: str | None = None,
) -> dict[str, Any]:
    """Field-level diff of two designs, then both judged on the same simulation and seed."""
    base_path, cand_path = design_path(base), design_path(candidate)
    rules_path = design_rules_path(rules)
    diff = design_diff(OrkDocument.load(base_path), OrkDocument.load(cand_path))
    with _openrocket:
        before = judge_file(base_path, simulation, seed, rules_path)
        after = judge_file(cand_path, simulation, seed, rules_path)
    metrics = {
        key: {
            "base": before.metrics[key],
            "candidate": after.metrics[key],
            "change": after.metrics[key] - before.metrics[key],
        }
        for key in before.metrics
        if key in after.metrics
    }
    states_before = {v.rule: v for v in before.verdicts}
    verdicts = []
    for v in after.verdicts:
        old = states_before.get(v.rule)
        verdicts.append(
            {
                "rule": v.rule,
                "title": v.title,
                "base": old.state if old else None,
                "candidate": v.state,
                "changed": old is None or old.state != v.state,
                "base_value": old.value if old else None,
                "candidate_value": v.value,
            }
        )
    return {
        "design_diff": diff,
        "metrics": metrics,
        "verdicts": verdicts,
        "base_passed": before.passed,
        "candidate_passed": after.passed,
        "advisory": ADVISORY,
        "provenance": {
            "base": provenance(base_path, before.backend, seed=before.seed),
            "candidate": provenance(cand_path, after.backend, seed=after.seed),
            "simulation": after.simulation,
        },
    }


def design_diff(a: OrkDocument, b: OrkDocument) -> dict[str, Any]:
    """What changed from a to b: component fields, added/removed components, motors."""
    before = {c.name: c for c in a.components()}
    after = {c.name: c for c in b.components()}
    changed = []
    for name, component in after.items():
        if name not in before:
            continue
        old, new = _leaf_values(before[name]), _leaf_values(component)
        for tag in sorted(old.keys() | new.keys()):
            if old.get(tag) != new.get(tag):
                changed.append(
                    {
                        "component": name,
                        "field": tag,
                        "base": old.get(tag),
                        "candidate": new.get(tag),
                    }
                )
    motors_a, motors_b = [asdict(m) for m in a.motors()], [asdict(m) for m in b.motors()]
    return {
        "changed_fields": changed,
        "added_components": [n for n in after if n not in before],
        "removed_components": [n for n in before if n not in after],
        "motors": None if motors_a == motors_b else {"base": motors_a, "candidate": motors_b},
    }


def _leaf_values(c: Component) -> dict[str, str]:
    values = {}
    for child in c.element:
        if len(child) or child.tag == "id":
            continue
        text = (child.text or "").strip()
        attributes = " ".join(f"{k}={v}" for k, v in sorted(child.attrib.items()))
        values[child.tag] = f"{text} [{attributes}]" if attributes else text
    return values


# --- optimizing ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OptimizePlan:
    base: Path
    variants: list[Variant]
    out_dir: Path
    nose: NoseWeight
    base_fin_height: float
    rules_path: Path | None


def optimize_plan(
    file: str,
    fins_mm: str = "30:45:1",
    nose_g: str = "0:30:5",
    nose_at_m: float | None = None,
    out_dir: str | None = None,
    rules: str | None = None,
) -> OptimizePlan:
    """Validate a sweep before any flying starts."""
    path = design_path(file)
    try:
        fins = [mm / 1000 for mm in parse_range(fins_mm)]
        noses = [g / 1000 for g in parse_range(nose_g)]
    except ValueError as error:
        raise ToolError(str(error)) from error
    if len(fins) * len(noses) > MAX_VARIANTS:
        raise ToolError(
            f"{len(fins) * len(noses)} variants is over the limit of {MAX_VARIANTS}; "
            "use a coarser step or a narrower range"
        )
    finsets = [c for c in OrkDocument.load(path).components() if c.kind == "trapezoidfinset"]
    if len(finsets) != 1:
        raise ToolError(
            f"the optimizer needs exactly one trapezoidal fin set; found {len(finsets)}"
        )
    folder = _inside_root(Path(out_dir) if out_dir else path.parent / "ogive" / f"opt-{path.stem}")
    foreign = (
        sorted(p.name for p in folder.iterdir() if p.name not in SWEEP_OUTPUTS)
        if (folder.is_dir())
        else []
    )
    if foreign:
        raise ToolError(
            f"{folder} holds other files ({', '.join(foreign[:5])}); a sweep may only reuse a "
            "folder that holds nothing but an earlier sweep's output. Pick a new out_dir."
        )
    return OptimizePlan(
        base=path,
        variants=[Variant(h, m) for h in fins for m in noses],
        out_dir=folder,
        nose=NoseWeight() if nose_at_m is None else NoseWeight(top_offset=nose_at_m),
        base_fin_height=finsets[0].number("height"),
        rules_path=design_rules_path(rules),
    )


def optimize_variant(
    plan: OptimizePlan, variant: Variant, simulation: str | None, seed: int
) -> Row:
    with _openrocket:
        return run_variant(
            plan.base, variant, plan.out_dir, simulation, seed, plan.rules_path, plan.nose
        )


def optimize_result(plan: OptimizePlan, rows: Sequence[Row], min_cal: float, goal_cal: float):
    """Rank the judged variants, write results.csv and pareto.svg, return the summary."""
    front = front_rows(rows)
    best, goal = lightest(rows), lightest(rows, goals=True)
    current = next(
        (
            r
            for r in rows
            if abs(r.variant.fin_height - plan.base_fin_height) < 5e-5 and r.variant.nose_mass == 0
        ),
        None,
    )
    marks = {
        name: r
        for name, r in (
            ("lightest passing", best),
            ("lightest meeting goals", goal),
            ("current design", current),
        )
        if r is not None
    }
    first = rows[0].report
    csv_path = output.write_csv(rows, front, plan.out_dir / "results.csv")
    svg_path = plan.out_dir / "pareto.svg"
    subtitle = (
        f'{plan.base.name}, sim "{first.simulation}", {first.backend}, seed {first.seed}, '
        f"{len(rows)} variants"
    )
    svg_path.write_text(
        output.pareto_svg(rows, front, marks, min_cal, goal_cal, subtitle), encoding="utf-8"
    )
    notes = [
        f"ASSUMPTION: nose weight is a slug {plan.nose.length * 1000:g} mm long, its front "
        f"{plan.nose.top_offset * 1000:g} mm behind the tip (nose_at_m).",
        f"ASSUMPTION: one wind seed ({first.seed}) per variant.",
    ]
    if best and best.variant.fin_height == min(v.fin_height for v in plan.variants):
        notes.append(
            "The lightest passing design uses the smallest fins in the grid; "
            "a smaller fin outside the grid might pass too."
        )
    failures: dict[str, int] = {}
    for r in rows:
        for rule in r.failures:
            failures[rule] = failures.get(rule, 0) + 1
    return {
        "variants": len(rows),
        "passed": sum(r.passed for r in rows),
        "failures_by_rule": failures,
        "lightest_passing": _row_record(best),
        "lightest_meeting_goals": _row_record(goal),
        "current_design": _row_record(current),
        "pareto_front": [_row_record(r) for r in front],
        "files": {"csv": str(csv_path), "plot": str(svg_path), "variants": str(plan.out_dir)},
        "notes": notes,
        "advisory": ADVISORY,
        "provenance": provenance(
            plan.base, first.backend, seed=first.seed, simulation=first.simulation
        ),
    }


def _row_record(r: Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "fin_height_mm": round(r.variant.fin_height * 1000, 6),
        "nose_weight_g": round(r.variant.nose_mass * 1000, 6),
        "passed": r.passed,
        "meets_goals": r.meets_goals,
        "failed_rules": r.failures,
        "metrics": r.report.metrics,
        "worst_ascent_stability_cal": r.measured("stability.ascent"),
        "flutter_margin": r.measured("flutter.margin"),
        "file": str(r.file),
    }
