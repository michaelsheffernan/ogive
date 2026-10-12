"""Command line for Ogive.

ogive tree FILE                      print the component tree, masses and stored sims
ogive set FILE --component NAME --field TAG --value X -o OUT
                                     change one numeric field (SI units) and save a copy
ogive sim FILE [--sim NAME] [--seed N] [--csv OUT]
                                     run a stored simulation headless in OpenRocket
ogive judge FILE [--sim NAME] [--seed N] [--rules RULES.toml]
                                     check a design against the judge rules (exit 1 on any FAIL)
ogive optimize FILE [--sim NAME] [--fins 30:45:1] [--nose 0:30:5] [-o DIR]
                                     sweep fin height (mm) and nose weight (g), judge every variant
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ogive.ork_io.components import Component, FieldValue, key_dimensions
from ogive.ork_io.document import OrkDocument, OrkFormatError
from ogive.ork_io.mass import BACKEND, CONTAINER_KINDS, component_mass, dry_mass
from ogive.ork_io.simulations import CURRENT, NEVER_RUN, OUTDATED, StoredSimulation
from ogive.physics.atmosphere import ABSOLUTE_ZERO_C

FIN_KIND = "trapezoidfinset"

VERDICTS = {
    CURRENT: "current (OpenRocket said so at save time)",
    OUTDATED: "OUTDATED: design or settings changed after this run",
    NEVER_RUN: "never run",
}
UNKNOWN_VERDICT = "freshness UNKNOWN (saved without sim data): verify before use"


def main(argv: list[str] | None = None) -> int:
    # Output has ° and ²; the Windows console defaults to cp1252.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (OrkFormatError, KeyError, ValueError, OSError) as error:
        message = error.args[0] if isinstance(error, KeyError) and error.args else error
        print(f"error: {message}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ogive", description="Judge OpenRocket designs and prove every change by simulation."
    )
    commands = parser.add_subparsers(required=True, metavar="COMMAND")

    tree = commands.add_parser("tree", help="print the component tree, masses and stored sims")
    tree.add_argument("file", type=Path)
    tree.set_defaults(handler=cmd_tree)

    edit = commands.add_parser("set", help="change one numeric field and save a copy")
    edit.add_argument("file", type=Path)
    edit.add_argument("--component", required=True, help="component name, e.g. 'Nose Cone'")
    edit.add_argument("--field", required=True, help="XML field, e.g. height")
    edit.add_argument("--value", required=True, type=float, help="SI units, as stored: m, kg")
    edit.add_argument("-o", "--output", required=True, type=Path)
    edit.set_defaults(handler=cmd_set)

    sim = commands.add_parser("sim", help="run a stored simulation headless in OpenRocket")
    sim.add_argument("file", type=Path)
    sim.add_argument("--sim", help="simulation name in the file (default: first)")
    sim.add_argument("--seed", type=int, help="wind seed; same seed = identical run")
    sim.add_argument("--csv", type=Path, help="write the time series here")
    sim.set_defaults(handler=cmd_sim)

    judge = commands.add_parser("judge", help="check a design against the judge rules")
    judge.add_argument("file", type=Path)
    judge.add_argument("--sim", help="simulation to fly (default: first in the file)")
    judge.add_argument("--seed", type=int, default=0, help="wind seed (default 0)")
    judge.add_argument("--rules", type=Path, help="TOML file overriding the default rules")
    judge.set_defaults(handler=cmd_judge)

    opt = commands.add_parser(
        "optimize", help="sweep fin height and nose weight, judge every variant"
    )
    opt.add_argument("file", type=Path)
    opt.add_argument("--sim", help="simulation to fly (default: first in the file)")
    opt.add_argument("--seed", type=int, default=0, help="wind seed (default 0)")
    opt.add_argument("--fins", default="30:45:1", help="fin height in mm, start:stop:step")
    opt.add_argument("--nose", default="0:30:5", help="nose weight in g, start:stop:step")
    opt.add_argument(
        "--nose-at",
        type=float,
        help="m from the nose tip to the front of the weight (ASSUMED default 0.10)",
    )
    opt.add_argument("--rules", type=Path, help="TOML file overriding the default rules")
    opt.add_argument("-o", "--out", type=Path, help="output folder (default out/opt/FILE_STEM)")
    opt.set_defaults(handler=cmd_optimize)
    return parser


def cmd_tree(args: argparse.Namespace) -> int:
    doc = OrkDocument.load(args.file)
    print(f"{args.file}  (format {doc.format_version}, {doc.creator})")
    components = doc.components()
    for c in components:
        print(_component_line(c))

    print()
    for motor in doc.motors():
        delay = "plugged" if motor.delay is None else f"delay {motor.delay:g} s"
        print(
            f"Motor: {motor.designation} ({motor.manufacturer}) in {motor.mount}, {delay}, "
            f"curve {motor.digest[:8]}"
        )
    summary = dry_mass(components)
    line = f"Dry mass: {summary.total * 1000:.1f} g  [{BACKEND}]"
    if summary.unmodelled:
        line += f"  (missing: {', '.join(summary.unmodelled)})"
    print(line)

    print()
    print("Stored simulations:")
    for sim in doc.simulations():
        print(_simulation_lines(sim))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    if args.output.resolve() == args.file.resolve():
        raise ValueError("refusing to overwrite the input file; pick another -o")
    doc = OrkDocument.load(args.file)
    component = doc.find_component(args.component)
    old = component.get(args.field)
    component.set(args.field, args.value)
    outdated = doc.mark_simulations_outdated()
    saved = doc.save(args.output)
    print(
        f"{component.name} [{component.kind}] <{args.field}>: "
        f"{_raw(old)} -> {args.value!r}  saved {saved}"
    )
    if outdated:
        print(f"{outdated} stored simulation(s) marked outdated: re-run them before trusting")
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    import csv

    from ogive.sim import openrocket

    try:
        result = openrocket.simulate(args.file, args.sim, args.seed)
    except openrocket.OpenRocketUnavailable as error:
        raise ValueError(str(error)) from error
    s, c = result.summary, result.conditions
    site = f"lat {c.latitude:g}, lon {c.longitude:g}"
    atmosphere = "ISA" if c.isa else f"{c.temperature + ABSOLUTE_ZERO_C:.1f} °C"
    print(f"{result.simulation}  [{result.backend}]  seed {result.seed}")
    print(f"  rod {c.rod_length:g} m, {site}, {atmosphere}, wind {c.wind_average:g} m/s")
    rows = [
        ("Apogee", f"{s.apogee:.1f} m"),
        ("Max velocity", f"{s.max_velocity:.1f} m/s  (M{s.max_mach:.3f})"),
        ("Max acceleration", f"{s.max_acceleration:.1f} m/s²"),
        ("Velocity off rod", f"{s.launch_rod_velocity:.1f} m/s"),
        ("Time to apogee", f"{s.time_to_apogee:.2f} s"),
        ("Optimum delay", f"{s.optimum_delay:.2f} s"),
        ("Deployment velocity", f"{s.deployment_velocity:.1f} m/s"),
        ("Flight time", f"{s.flight_time:.1f} s"),
        ("Ground hit velocity", f"{s.ground_hit_velocity:.2f} m/s (total speed)"),
    ]
    for k, v in rows:
        print(f"  {k:<22}{v}")
    print(
        "  Events: "
        + ", ".join(f"{e.type} {e.time:.2f} s" for e in result.events if e.type != "Warning")
    )
    for w in result.warnings:
        print(f"  warning: {w}")
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            keys = list(result.series)
            writer.writerow(keys)
            writer.writerows(zip(*(result.series[k] for k in keys), strict=True))
        print(f"  series: {args.csv} ({len(result.series['time'])} rows)")
    return 0


MARK = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "pending": "....."}


def cmd_judge(args: argparse.Namespace) -> int:
    from ogive.judge import judge_file
    from ogive.sim import openrocket

    try:
        report = judge_file(args.file, args.sim, args.seed, args.rules)
    except openrocket.OpenRocketUnavailable as error:
        raise ValueError(str(error)) from error
    print(f'{report.file}  sim "{report.simulation}"  seed {report.seed}  [{report.backend}]')
    print("Advisory only: OpenRocket and RASAero II sign off on real flights.")
    print()
    for v in report.verdicts:
        print(f"  {MARK[v.state]:<5}  {v.title}")
        print(f"         {v.value}   (limit {v.limit})   [{v.source}]")
        if v.detail:
            print(f"         {v.detail}")
        for a in v.assumptions:
            print(f"         ASSUMPTION: {a}")
    counts = ", ".join(f"{report.count(s)} {s}" for s in ("pass", "warn", "fail", "pending"))
    print()
    print(f"  {'PASSED' if report.passed else 'FAILED'}: {counts}")
    return 0 if report.passed else 1


def cmd_optimize(args: argparse.Namespace) -> int:
    from ogive.judge import load_rules
    from ogive.opt import output
    from ogive.opt.sweep import NoseWeight, Row, front_rows, lightest, parse_range, sweep
    from ogive.sim import openrocket

    fins = [mm / 1000 for mm in parse_range(args.fins)]
    noses = [g / 1000 for g in parse_range(args.nose)]
    out = args.out or Path("out") / "opt" / args.file.stem
    rules = load_rules(args.rules)
    nose = NoseWeight() if args.nose_at is None else NoseWeight(top_offset=args.nose_at)
    (base_fins,) = [c for c in OrkDocument.load(args.file).components() if c.kind == FIN_KIND]
    base_height = base_fins.number("height")

    print(
        f"{args.file}: {len(fins)} fin heights x {len(noses)} nose weights = "
        f"{len(fins) * len(noses)} variants, each flown in OpenRocket and judged"
    )
    print(
        f"ASSUMPTION: nose weight is a slug {nose.length * 1000:g} mm long, its front "
        f"{nose.top_offset * 1000:g} mm behind the tip. Change with --nose-at."
    )
    print(f"ASSUMPTION: one wind seed ({args.seed}) per variant.")
    print()

    def show(row: Row) -> None:
        print(
            f"  {_short_row(row)}  {'PASS' if row.passed else 'FAIL'}  {_numbers(row)}"
            + (f"  fails: {', '.join(row.failures)}" if row.failures else "")
        )

    try:
        rows = sweep(args.file, fins, noses, out, args.sim, args.seed, args.rules, nose, show)
    except openrocket.OpenRocketUnavailable as error:
        raise ValueError(str(error)) from error

    front = front_rows(rows)
    best, goal = lightest(rows), lightest(rows, goals=True)
    current = next(
        (
            r
            for r in rows
            if abs(r.variant.fin_height - base_height) < 5e-5 and r.variant.nose_mass == 0
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
    subtitle = (
        f'{args.file.name}, sim "{first.simulation}", {first.backend}, seed {first.seed}, '
        f"{len(rows)} variants"
    )
    csv_path = output.write_csv(rows, front, out / "results.csv")
    svg_path = out / "pareto.svg"
    svg_path.write_text(
        output.pareto_svg(
            rows,
            front,
            marks,
            rules["stability"]["min_cal"],
            rules["stability"]["goal_cal"],
            subtitle,
        ),
        encoding="utf-8",
    )

    print()
    print(
        f"[{first.backend}, seed {first.seed}]  {sum(r.passed for r in rows)} of {len(rows)} pass"
    )
    for name, r in (
        ("Lightest passing", best),
        ("Lightest meeting goals", goal),
        ("Current design", current),
    ):
        if r is None:
            print(f"  {name + ':':<24}none in this grid")
        else:
            state = "PASS" if r.passed else "FAIL"
            print(f"  {name + ':':<24}{_short_row(r)}  {state}  {_numbers(r)}")
    if best and best.variant.fin_height == min(fins):
        print("  NOTE: the lightest passing design uses the smallest fins in the grid.")
        print("        A smaller fin outside the grid might pass too. Widen --fins to check.")
    print("  Pareto front, max Mach against worst ascent stability:")
    for r in front:
        print(f"    {_short_row(r)}  {_numbers(r)}")
    print(f"  Files: {csv_path}, {svg_path}, {out / 'variants'} (open any in OpenRocket)")
    print("Advisory only: OpenRocket and RASAero II sign off on real flights.")
    return 0 if best else 1


def _short_row(row) -> str:
    v = row.variant
    return f"fins {v.fin_height * 1000:>4g} mm, nose {v.nose_mass * 1000:>3g} g"


def _numbers(row) -> str:
    stab, flutter = row.measured("stability.ascent"), row.measured("flutter.margin")
    return (
        f"{row.metric('launch_mass') * 1000:.0f} g  M{row.metric('max_mach'):.3f}  "
        f"apogee {row.metric('apogee'):.0f} m  "
        f"stability min {'—' if stab is None else f'{stab:.2f}'} cal  "
        f"flutter {'—' if flutter is None else f'{flutter:.2f}'}x"
    )


def _component_line(c: Component) -> str:
    line = f"{'  ' * c.depth}{c.name or '(unnamed)'} [{c.kind}]"
    dims = _format_dims(key_dimensions(c))
    if dims:
        line += f"  {dims}"
    if c.kind not in CONTAINER_KINDS:
        line += f"  | {_format_mass(c)}"
    return line


def _format_dims(dims: dict[str, float | str]) -> str:
    parts = []
    for key, value in dims.items():
        if key == "mass":
            continue  # shown in the mass column
        if isinstance(value, str):
            parts.append(value)
        elif key == "fins":
            parts.append(f"{value:.0f} fins")
        else:
            parts.append(f"{key} {value * 1000:.1f} mm")
    return ", ".join(parts)


def _format_mass(c: Component) -> str:
    mass = component_mass(c)
    return "mass: not modelled" if mass is None else f"{mass * 1000:.1f} g"


def _simulation_lines(sim: StoredSimulation) -> str:
    verdict = VERDICTS.get(sim.freshness, UNKNOWN_VERDICT)
    cond = sim.conditions
    rod = cond.get("launchrodlength", "?")
    atmosphere = _format_atmosphere(cond)
    site = f"lat {cond.get('launchlatitude', '?')}, lon {cond.get('launchlongitude', '?')}"
    nan = float("nan")
    velocity = sim.flight_data.get("maxvelocity", nan)
    apogee = sim.flight_data.get("maxaltitude", nan)
    mach = sim.flight_data.get("maxmach", nan)
    return "\n".join(
        [
            f"  {sim.name}: status {sim.status} -> {verdict}",
            f"    rod {rod} m, atmosphere {atmosphere}, {site}",
            f"    max velocity {velocity:.1f} m/s, apogee {apogee:.1f} m, max Mach {mach:.3f}"
            "  [OpenRocket, stored]",
        ]
    )


def _format_atmosphere(cond: dict[str, str]) -> str:
    model = cond.get("atmosphere", "?")
    if model == "isa":
        return "ISA"
    temperature_k = cond.get("atmosphere.basetemperature")
    pressure_pa = cond.get("atmosphere.basepressure")
    if model == "extendedisa" and temperature_k and pressure_pa:
        celsius = float(temperature_k) + ABSOLUTE_ZERO_C
        return f"{celsius:.1f} °C, {float(pressure_pa) / 100:.2f} hPa"
    return model


def _raw(value: FieldValue) -> str:
    return f"auto {value.value!r}" if value.auto else repr(value.value)


if __name__ == "__main__":
    raise SystemExit(main())
