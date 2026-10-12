"""Run the judge on an .ork: gather evidence from ork_io and OpenRocket, apply every check."""

from __future__ import annotations

from pathlib import Path

from ogive.judge import checks
from ogive.judge.model import Report, load_rules
from ogive.ork_io import OrkDocument
from ogive.ork_io.components import Component
from ogive.ork_io.simulations import OUTDATED
from ogive.physics.flutter import Fin
from ogive.sim import openrocket


def ascent_window(result: openrocket.FlightResult, min_mach: float) -> list[int]:
    """Indices from rod clearance to the ejection charge (or apogee) while Mach > min_mach."""
    t0 = result.event_time("Launch rod clearance") or 0.0
    t1 = result.event_time("Ejection charge") or result.event_time("Apogee") or float("inf")
    s = result.series
    return [
        i
        for i, t in enumerate(s["time"])
        if t0 <= t <= t1 and s["mach"][i] > min_mach and s["stability"][i] == s["stability"][i]
    ]


def fin_of(doc: OrkDocument) -> Fin | None:
    fins = [c for c in doc.components() if c.kind == "trapezoidfinset"]
    if not fins:
        return None
    f = fins[0]
    return Fin(
        f.number("height"), f.number("rootchord"), f.number("tipchord"), f.number("thickness")
    )


def mass_override_traps(doc: OrkDocument) -> list[str]:
    def covers_children(c: Component) -> bool:
        return c.has("overridesubcomponentsmass") and c.text("overridesubcomponentsmass") == "true"

    def has_children(c: Component) -> bool:
        return (
            c.element.find("subcomponents") is not None and len(c.element.find("subcomponents")) > 0
        )

    return [
        c.name
        for c in doc.components()
        if c.has("overridemass") and has_children(c) and not covers_children(c)
    ]


def judge_file(
    path: str | Path,
    simulation: str | None = None,
    seed: int = 0,
    rules_path: str | Path | None = None,
) -> Report:
    rules = load_rules(rules_path)
    path = Path(path)
    doc = OrkDocument.load(path)
    names = [s.name for s in doc.simulations()]
    simulation = simulation or (names[0] if names else None)
    if simulation is None:
        raise ValueError(f"{path.name} has no simulations to judge")
    stored = next((s for s in doc.simulations() if s.name == simulation), None)

    static = openrocket.static_analysis(path)
    loaded = openrocket.load(path)
    flight = openrocket.simulate(path, simulation, seed)
    src = flight.backend
    s = flight.summary
    (motor,) = loaded.motors

    window = ascent_window(flight, rules["stability"]["window_min_mach"])
    series = flight.series
    stab_samples = [(series["time"][i], series["stability"][i], series["mach"][i]) for i in window]
    flutter_samples = [
        (
            series["time"][i],
            series["velocity"][i],
            series["pressure"][i],
            series["speed_of_sound"][i],
        )
        for i in window
    ]

    verdicts = [
        checks.static_stability(static.calibers, rules, f"{src} Barrowman"),
        checks.ascent_stability(stab_samples, rules, f"{src} flight"),
        checks.rod_velocity(s.launch_rod_velocity, rules, f"{src} flight"),
    ]
    fin = fin_of(doc)
    if fin is not None:
        verdicts.append(checks.flutter(fin, flutter_samples, rules, src))
    verdicts += [
        checks.ground_hit(s.ground_hit_velocity, rules, f"{src} flight"),
        checks.deployment_warnings(flight.warnings, f"{src} warnings"),
        checks.motor_designation(
            motor.designation,
            motor.total_impulse,
            motor.average_thrust,
            rules,
            f"{src} motor database",
        ),
    ]
    # A summary already marked outdated (by OpenRocket or an Ogive edit) claims nothing.
    outdated = stored is not None and stored.freshness == OUTDATED
    fd = stored.flight_data if stored and not outdated else {}
    missing = "stored results are marked outdated" if outdated else "no stored summary"
    implied = fd["timetoapogee"] - fd["optimumdelay"] if fd.get("timetoapogee") else None
    verdicts += [
        checks.stored_burn_time(
            motor.designation, implied, rules, "stored summary in the .ork", missing
        ),
        checks.stored_matches_rerun(
            fd.get("maxaltitude"),
            fd.get("maxvelocity"),
            s.apogee,
            s.max_velocity,
            rules,
            f"stored summary vs {src} re-run",
            missing,
        ),
        checks.rocket_equation(
            s.max_velocity,
            motor.total_impulse,
            static.mass_with_motors,
            motor.launch_mass - motor.burnout_mass,
            motor.burn_time,
            f"{src} motor + flight",
        ),
        checks.mass_consistency(
            static.mass_without_motors,
            motor.launch_mass,
            static.mass_with_motors,
            f"{src} MassCalculator",
        ),
        checks.fineness(static.length, static.max_diameter, rules, f"{src} geometry"),
        checks.supersonic(s.max_mach, rules, f"{src} flight"),
        checks.mass_override_traps(mass_override_traps(doc), "Ogive ork_io"),
    ]
    metrics = {
        "apogee": s.apogee,
        "max_velocity": s.max_velocity,
        "max_mach": s.max_mach,
        "launch_mass": static.mass_with_motors,
        "dry_mass": static.mass_without_motors,
    }
    return Report(str(path), simulation, flight.seed, src, verdicts, metrics)
