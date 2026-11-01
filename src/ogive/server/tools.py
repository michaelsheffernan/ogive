"""Ogive's tools as plain functions: JSON-ready dicts out, no MCP import.

The MCP server (app.py) wraps these. Every result that holds numbers carries `provenance`:
the backend that produced them, the seed, and the exact file they came from (sha256).

Files: tools read only existing .ork files and write only NEW .ork files (never overwrite).
Set OGIVE_ROOT to confine every path to one folder.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ogive import __version__
from ogive.judge import FAIL, WARN, Report, judge_file
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


def user_path(text: str) -> Path:
    """A path from the client. Relative paths resolve inside OGIVE_ROOT when it is set.

    MCP clients start the server in a folder of their choosing (Claude Desktop: the bundle's own
    folder), so "rocket.ork" must mean the designs folder, not the server's working directory.
    Absolute paths are kept. Either way the result must stay inside OGIVE_ROOT.
    """
    path = Path(text).expanduser()
    root = os.environ.get(ROOT_ENV)
    if root and not path.is_absolute():
        path = Path(root) / path
    return _inside_root(path)


def design_path(file: str) -> Path:
    """An existing .ork file, inside OGIVE_ROOT when that is set."""
    path = user_path(file)
    if path.suffix.lower() != ".ork":
        raise ToolError(f"{path.name} is not an .ork file")
    if not path.is_file():
        raise ToolError(f"no such file: {path}")
    return path


def new_output_path(output: str | None, source: Path) -> Path:
    """Where to write a new design. Default: SOURCE_DIR/ogive/STEM-001.ork, first free number."""
    if output:
        path = user_path(output)
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


def pick_simulation(path: Path, simulation: str | None) -> str | None:
    """The simulation to fly: the one the call names; else $OGIVE_SIMULATION when this file has
    one by that name (a team's reference sim); else None, which means the file's first.

    Call with the OpenRocket lock held.
    """
    if simulation:
        return simulation
    preferred = os.environ.get("OGIVE_SIMULATION", "").strip()
    if preferred and preferred in openrocket.list_simulations(path):
        return preferred
    return None


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
        result = openrocket.simulate(path, pick_simulation(path, simulation), seed)
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
        report = judge_file(path, pick_simulation(path, simulation), seed, rules_path)
    return report_record(report, path, rules_path)


def design_rules_path(rules: str | None) -> Path | None:
    if rules is None:
        return None
    path = user_path(rules)
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


# --- motors -------------------------------------------------------------------------------

CURVE_TOLERANCE = 0.05  # a curve's integrated impulse must be within 5 % of its designation


def choose_curve(
    curves: Sequence[openrocket.MotorCurve], designation: str
) -> tuple[openrocket.MotorCurve, str]:
    """The curve whose integrated impulse is closest to the designation's number, within 5 %.

    The name alone can't be trusted: OpenRocket 24.12 files a 316 Ns curve under 229H255-14A.
    Returns the curve and a one-line reason.
    """
    from ogive.judge.checks import DESIGNATION

    match = DESIGNATION.match(designation.strip().upper())
    if not match:
        raise ToolError(f"can't read the total impulse from {designation!r}; expected e.g. 282H399")
    nominal = float(match["impulse"])
    if not curves:
        raise ToolError(f"{designation} is not in OpenRocket's motor database")
    best = min(curves, key=lambda c: abs(c.total_impulse / nominal - 1))
    error = best.total_impulse / nominal - 1
    summary = ", ".join(
        f"{c.designation} {c.total_impulse:.1f} Ns ({c.digest[:8]})" for c in curves
    )
    if abs(error) > CURVE_TOLERANCE:
        raise ToolError(
            f"no {designation} curve within {CURVE_TOLERANCE:.0%} of {nominal:g} Ns: {summary}"
        )
    reason = (
        f"{len(curves)} curve(s) for {designation}: {summary}. Chose {best.digest[:8]}: "
        f"{best.total_impulse:.1f} Ns is {error:+.1%} from the {nominal:g} Ns in the name"
    )
    return best, reason


def pick_delay(curve: openrocket.MotorCurve, wanted: float | None) -> tuple[float | None, str]:
    """Keep the wanted delay if the motor can do it; otherwise its longest standard delay.

    Cesaroni's "-14A" style delays are adjustable down from the longest, so anything up to the
    longest standard delay is achievable. None means a plugged motor (no ejection charge).
    """
    if wanted is None:
        return None, "plugged (no ejection charge), as before"
    longest = max(curve.delays) if curve.delays else wanted
    if wanted <= longest:
        return wanted, f"{wanted:g} s kept (motor's longest standard delay is {longest:g} s)"
    return longest, f"{wanted:g} s isn't available; used the longest, {longest:g} s"


def set_motor(
    file: str, designation: str, output: str | None = None, delay: float | None = None
) -> dict[str, Any]:
    """Swap the motor for a curve from OpenRocket's database; save a NEW file.

    Picks the curve by integrated impulse (choose_curve), keeps the ejection delay when the motor
    allows it, lengthens the motor mount if the new case doesn't fit, and reloads the result in
    OpenRocket to prove the digest resolves to the chosen curve.
    """
    path = design_path(file)
    doc = OrkDocument.load(path)
    mounts = [(c, m) for c in doc.components() for m in c.element.iterfind("motormount/motor")]
    if len(mounts) != 1:
        raise ToolError(f"set_motor needs exactly one motor in the file; found {len(mounts)}")
    mount, element = mounts[0]
    with _openrocket:
        curves = openrocket.find_motors(designation)
    curve, reason = choose_curve(curves, designation)
    old = {tag: (element.findtext(tag) or "").strip() for tag in ("designation", "digest", "delay")}
    current_delay = None if old["delay"] in ("", "none") else float(old["delay"])
    new_delay, delay_note = pick_delay(curve, current_delay if delay is None else delay)

    fields = {
        "type": curve.type,
        "manufacturer": curve.manufacturer,
        "digest": curve.digest,
        "designation": curve.designation,
        "diameter": repr(curve.diameter),
        "length": repr(curve.length),
        "delay": "none" if new_delay is None else repr(float(new_delay)),
    }
    for tag, text in fields.items():
        child = element.find(tag)
        if child is None:
            raise ToolError(f"the motor element has no <{tag}>; can't edit it safely")
        child.text = text

    mount_note = "mount unchanged"
    overhang = float((mount.element.findtext("motormount/overhang") or "0").strip() or 0)
    mount_length = mount.number("length")
    if curve.length - overhang > mount_length:
        mount.set("length", curve.length - overhang)
        mount_note = (
            f"{mount.name} lengthened {mount_length * 1000:.1f} -> "
            f"{(curve.length - overhang) * 1000:.1f} mm to fit the {curve.case} case"
        )
    record = _save_new(
        doc,
        path,
        output,
        {
            "motor": asdict(curve),
            "replaced": old,
            "choice": reason,
            "delay": delay_note,
            "mount": mount_note,
        },
    )
    with _openrocket:
        (loaded,) = openrocket.load(record["output"]).motors
    if loaded.digest != curve.digest:
        raise ToolError(
            f"OpenRocket loaded curve {loaded.digest[:8]}, not the chosen {curve.digest[:8]}; "
            f"the new file is {record['output']}"
        )
    record["verified"] = (
        f"OpenRocket loads {loaded.designation} ({loaded.digest[:8]}, "
        f"{loaded.total_impulse:.1f} Ns) from the new file"
    )
    return record


# --- drawing ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Render:
    record: dict[str, Any]  # numbers on the drawing, with provenance
    png: bytes
    svg: str


def render_design(file: str, assumed: Sequence[str] = ()) -> Render:
    """To-scale side view from OpenRocket's model: PNG for the chat, SVG to keep.

    assumed: component names, or "Component.field", to draw hatched and tagged ASSUMED
    (components whose OpenRocket comment says "ASSUMED" are marked too).
    """
    from ogive.viewer import side_view as drawing

    view, scene, record = _side_view(file, assumed)
    return Render(record, drawing.to_png(scene, palette=256), drawing.to_svg(scene))


def view_design(file: str, assumed: Sequence[str] = ()) -> tuple[dict[str, Any], Render]:
    """The 3D viewer's data plus the side view, from one read of the file.

    The viewer gets OpenRocket's geometry in metres (bodies as profiles, fin outlines with each
    instance's roll angle, parts and motors as cylinders), CG, CP and masses. The side view is
    for hosts that can't show in-chat apps.
    """
    from ogive.viewer import side_view as drawing

    view, scene, record = _side_view(file, assumed)
    data = {"kind": "design", **_model_data(view, scene, record)}
    return data, Render(record, drawing.to_png(scene, palette=256), drawing.to_svg(scene))


MAX_VERSIONS = 12


def view_versions(
    files: Sequence[str],
    simulation: str | None = None,
    seed: int = 0,
    rules: str | None = None,
) -> tuple[dict[str, Any], Render]:
    """Versions of a design, in order, for the viewer's timeline.

    Each version: OpenRocket's model and static numbers, a judged flight (the same simulation,
    by name, and the same seed for all), and the field-level changes from the version before and
    from the first. The side view of the last version is for hosts without in-chat apps.
    """
    from ogive.viewer import side_view as drawing

    if not 2 <= len(files) <= MAX_VERSIONS:
        raise ToolError(f"give 2 to {MAX_VERSIONS} designs, oldest first; got {len(files)}")
    paths = [design_path(f) for f in files]
    rules_path = design_rules_path(rules)
    docs = [OrkDocument.load(p) for p in paths]
    with _openrocket:
        simulation = pick_simulation(paths[0], simulation)
        if simulation is None:
            first = openrocket.list_simulations(paths[0])
            if not first:
                raise ToolError(f"{paths[0].name} has no simulations")
            simulation = first[0]
        flown = []
        for path in paths:
            names = openrocket.list_simulations(path)
            if not names:
                raise ToolError(f"{path.name} has no simulations")
            flown.append(simulation if simulation in names else names[0])
    versions = []
    for i, (file, path, doc) in enumerate(zip(files, paths, docs, strict=True)):
        view, scene, record = _side_view(file, ())
        with _openrocket:
            report = judge_file(path, flown[i], seed, rules_path)
        versions.append(
            {
                **_model_data(view, scene, record),
                "simulation": report.simulation,
                "flight": {k: report.metrics[k] for k in ("apogee", "max_velocity", "max_mach")},
                "passed": report.passed,
                "fails": [f"{v.title}: {v.value}" for v in report.verdicts if v.state == FAIL],
                "warns": [f"{v.title}: {v.value}" for v in report.verdicts if v.state == WARN],
                "diff_previous": None if i == 0 else design_diff(docs[i - 1], doc),
                "diff_first": None if i == 0 else design_diff(docs[0], doc),
                "provenance": provenance(path, report.backend, seed=report.seed),
            }
        )
    different = [v["title"] for v in versions if v["simulation"] != simulation]
    data = {
        "kind": "versions",
        "simulation": simulation,
        "seed": seed,
        "different_simulation": different,
        "versions": versions,
        "advisory": ADVISORY,
    }
    summary = {
        "simulation": simulation,
        "seed": seed,
        "different_simulation": different,
        "versions": [
            {
                "file": v["title"],
                "passed": v["passed"],
                "fails": v["fails"],
                "apogee_m": round(v["flight"]["apogee"], 1),
                "max_mach": round(v["flight"]["max_mach"], 3),
                "stability_cal": round(v["stability"], 2),
                "stability_burnout_cal": round(v["stability_burnout"], 2),
                "launch_mass_g": round(v["launch_mass"] * 1000, 1),
                "changes_from_previous": v["diff_previous"],
                "sha256": v["provenance"]["sha256"],
            }
            for v in versions
        ],
        "advisory": ADVISORY,
    }
    return data, Render(summary, drawing.to_png(scene, palette=256), drawing.to_svg(scene))


REPLAY_SERIES = (
    "time", "altitude", "east", "north", "velocity", "vertical_velocity", "mach", "acceleration",
    "theta", "phi", "cg", "thrust", "stability",
)  # fmt: skip
REPLAY_EVENTS = (
    "Lift-off", "Launch rod clearance", "Motor burnout", "Ejection charge", "Stage separation",
    "Apogee", "Recovery device deployment", "Tumbling", "Ground hit",
)  # fmt: skip
REPLAY_LABEL = "Replay of simulation data, not footage."


def replay_flight(
    file: str, simulation: str | None = None, seed: int = 0, max_points: int = 2000
) -> tuple[dict[str, Any], Render]:
    """One simulated flight for the viewer's replay, every number from OpenRocket.

    Trajectory (east, north, altitude), the axis attitude (theta = elevation above the horizon,
    phi = azimuth from north toward east), speed, Mach, acceleration, CG, thrust and stability
    over time, plus the flight events. Derived here, from the same data: roll angle (integral of
    OpenRocket's roll rate) and the Mach 1 crossings (linear between samples). OpenRocket stops
    simulating attitude at the first recovery deployment; `attitude_until` says when.
    """
    from ogive.viewer import side_view as drawing

    path = design_path(file)
    view, scene, record = _side_view(file, ())
    with _openrocket:
        flight = openrocket.simulate(path, pick_simulation(path, simulation), seed)
    s = flight.series
    series = {name: s[name] for name in REPLAY_SERIES}
    series["roll"] = _integrate(s["time"], s["roll_rate"])
    kinds = {part["name"]: part["kind"] for part in view.model["parts"]}  # Parachute, Streamer
    events = [
        {"time": e.time, "type": e.type, "source": e.source, "source_kind": kinds.get(e.source, "")}
        for e in flight.events
        if e.type in REPLAY_EVENTS
    ]
    crossings = mach_crossings(s["time"], s["mach"])
    deployed = [e.time for e in flight.events if e.type == "Recovery device deployment"]
    source = provenance(path, flight.backend, seed=flight.seed, simulation=flight.simulation)
    data = {
        "kind": "flight",
        **_model_data(view, scene, record),
        "label": REPLAY_LABEL,
        "simulation": flight.simulation,
        "seed": flight.seed,
        "conditions": asdict(flight.conditions),
        "summary": asdict(flight.summary),
        "series": _finite(_downsample(series, max_points)),
        "events": events,
        "mach_crossings": crossings,
        "attitude_until": deployed[0] if deployed else None,
        "warnings": flight.warnings,
        "provenance": source,
    }
    text = {
        "label": REPLAY_LABEL,
        "summary": {k: round(v, 3) for k, v in asdict(flight.summary).items()},
        "events": [{**e, "time": round(e["time"], 3)} for e in events],
        "mach_1": [{"time_s": round(c["time"], 3), "direction": c["direction"]} for c in crossings],
        "attitude_simulated_until_s": data["attitude_until"],
        "warnings": flight.warnings,
        "notes": [
            "Attitude after the first recovery deployment is not simulated by OpenRocket.",
            "Roll angle is integrated from OpenRocket's roll rate; Mach 1 times are interpolated.",
        ],
        "provenance": source,
    }
    return data, Render(text, drawing.to_png(scene, palette=256), drawing.to_svg(scene))


def mach_crossings(time: Sequence[float], mach: Sequence[float]) -> list[dict[str, Any]]:
    """Times the Mach series crosses 1, interpolated linearly between samples."""
    out = []
    for i in range(1, len(time)):
        a, b = mach[i - 1], mach[i]
        if not (math.isfinite(a) and math.isfinite(b)) or a == b:
            continue
        if a < 1 <= b or b < 1 <= a:
            f = (1 - a) / (b - a)
            out.append(
                {
                    "time": time[i - 1] + f * (time[i] - time[i - 1]),
                    "direction": "up" if b > a else "down",
                }
            )
    return out


def _integrate(time: Sequence[float], rate: Sequence[float]) -> list[float]:
    """Running trapezoid integral; non-finite rates count as zero."""
    total, out = 0.0, [0.0]
    for i in range(1, len(time)):
        a = rate[i - 1] if math.isfinite(rate[i - 1]) else 0.0
        b = rate[i] if math.isfinite(rate[i]) else 0.0
        total += (a + b) / 2 * (time[i] - time[i - 1])
        out.append(total)
    return out


def _finite(series: dict[str, list[float]]) -> dict[str, list[float | None]]:
    """For the viewer: 6 significant digits (1 cm at 2 km), and null for missing samples (JSON
    has no NaN). The page only draws these; the text result keeps full precision."""
    return {
        k: [float(f"{x:.6g}") if math.isfinite(x) else None for x in v] for k, v in series.items()
    }


def _model_data(view: Any, scene: Any, record: dict[str, Any]) -> dict[str, Any]:
    """One design for the viewer: OpenRocket's model in metres plus CG, CP, masses, stability."""
    s = view.static
    return {
        "title": Path(record["provenance"]["file"]).name,
        "length": view.length,
        "max_diameter": s.max_diameter,
        "launch_mass": s.mass_with_motors,
        "dry_mass": s.mass_without_motors,
        "cg": s.cg_x,
        "cg_burnout": s.cg_burnout_x,
        "cp": s.cp_x,
        "mach": s.mach,
        "stability": s.calibers,
        "stability_burnout": s.burnout_calibers,
        "motors": view.motors,
        "assumed": scene.assumed,
        "not_drawn": view.not_drawn,
        "model": view.model,
        "provenance": record["provenance"],
    }


def _side_view(file: str, assumed: Sequence[str]) -> tuple[Any, Any, dict[str, Any]]:
    """Read the file in OpenRocket and lay out the side view: (SideView, Scene, record)."""
    from ogive.viewer import side_view as drawing

    path = design_path(file)
    with _openrocket:
        view = openrocket.side_view(path)
    record_provenance = provenance(path, f"{view.backend} model")
    try:
        scene = drawing.build(
            view, path.name, record_provenance["sha256"], __version__, set(assumed)
        )
    except ValueError as error:
        raise ToolError(str(error)) from error
    s = view.static
    record = {
        "length_mm": round(view.length * 1000, 2),
        "max_diameter_mm": round(s.max_diameter * 1000, 2),
        "launch_mass_g": round(s.mass_with_motors * 1000, 1),
        "dry_mass_g": round(s.mass_without_motors * 1000, 1),
        "cg_mm": round(s.cg_x * 1000, 1),
        "cg_burnout_mm": round(s.cg_burnout_x * 1000, 1),
        "cp_mm": round(s.cp_x * 1000, 1),
        "stability_cal": round(s.calibers, 3),
        "stability_burnout_cal": round(s.burnout_calibers, 3),
        "fins": [asdict(f) for f in view.fins],
        "motors": view.motors,
        "components": sorted({o.name for o in view.outlines if o.role != "motor"}),
        "assumed": scene.assumed,
        "not_drawn": view.not_drawn,
        "notes": ["Positions in mm from the nose tip. CP at M0.30, AOA 0 (Barrowman)."],
        "provenance": record_provenance,
    }
    return view, scene, record


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
        simulation = pick_simulation(base_path, simulation)
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
    folder = (
        user_path(out_dir) if out_dir else _inside_root(path.parent / "ogive" / f"opt-{path.stem}")
    )
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
        simulation = pick_simulation(plan.base, simulation)
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
