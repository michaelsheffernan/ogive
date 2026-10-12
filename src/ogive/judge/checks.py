"""Judge checks. Each takes plain numbers and returns one Verdict.

Plain numbers in, so every check can be tested without OpenRocket.

The `source` argument names the backend that produced the numbers being judged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ogive.judge.model import FAIL, PASS, PENDING, WARN, Verdict
from ogive.physics.flutter import Fin, flutter_velocity

G0 = 9.80665  # m/s^2, standard gravity
DESIGNATION = re.compile(r"^(?P<impulse>\d+(?:\.\d+)?)[A-Z](?P<thrust>\d+(?:\.\d+)?)")


def static_stability(calibers: float, rules: dict, source: str) -> Verdict:
    s = rules["stability"]
    state = FAIL if calibers < s["min_cal"] else WARN if calibers < s["goal_cal"] else PASS
    return Verdict(
        "stability.static",
        "Stability at M0.30 (OpenRocket's design view)",
        state,
        f"{calibers:.2f} cal",
        f"≥ {s['min_cal']} (goal {s['goal_cal']})",
        source,
        measured=calibers,
    )


def ascent_stability(
    samples: Sequence[tuple[float, float, float]], rules: dict, source: str
) -> Verdict:
    """samples: (time s, stability cal, Mach) inside the ascent window."""
    s = rules["stability"]
    if not samples:
        return Verdict(
            "stability.ascent",
            "Stability through the ascent",
            PENDING,
            "—",
            f"≥ {s['min_cal']}",
            source,
            "no samples in the ascent window",
        )
    t, worst, mach = min(samples, key=lambda x: x[1])
    best = max(x[1] for x in samples)
    state = FAIL if worst < s["min_cal"] else WARN if worst < s["goal_cal"] else PASS
    return Verdict(
        "stability.ascent",
        "Stability through the ascent (rod clearance to ejection)",
        state,
        f"min {worst:.2f} cal",
        f"≥ {s['min_cal']} (goal {s['goal_cal']})",
        source,
        f"lowest at t = {t:.2f} s (M{mach:.2f}); highest {best:.2f} cal",
        measured=worst,
    )


def rod_velocity(velocity: float, rules: dict, source: str) -> Verdict:
    limit = rules["launch"]["min_rod_velocity"]
    return Verdict(
        "launch.rod_velocity",
        "Speed off the launch rod",
        PASS if velocity >= limit else FAIL,
        f"{velocity:.1f} m/s",
        f"≥ {limit:g} m/s",
        source,
        measured=velocity,
    )


def flutter(
    fin: Fin,
    samples: Sequence[tuple[float, float, float, float]],
    rules: dict,
    source: str,
) -> Verdict:
    """samples: (time s, airspeed m/s, static pressure Pa, speed of sound m/s), ascent window.

    Margin = flutter speed / airspeed at every sample; the verdict uses the worst moment and the
    lowest shear modulus in the rules (conservative).
    """
    f = rules["flutter"]
    moduli = sorted(f["shear_modulus_gpa"])
    flying = [x for x in samples if x[1] > 1.0]
    if not flying:
        return Verdict(
            "flutter.margin",
            "Fin flutter margin",
            PENDING,
            "—",
            f"≥ {f['min_margin']}x",
            source,
            "no airspeed samples",
        )
    worst_by_g = {}
    for g in moduli:
        margins = [(flutter_velocity(fin, g * 1e9, a, p) / v, t, v) for t, v, p, a in flying]
        worst_by_g[g] = min(margins)
    margin, t, v = worst_by_g[moduli[0]]
    assumptions = ()
    if f.get("shear_modulus_source", "assumed") == "assumed":
        assumptions = (
            f"fin shear modulus ASSUMED {moduli[0]:g} GPa (range {moduli[0]:g}-{moduli[-1]:g}); "
            "replace with a datasheet or measured value",
        )
    detail = "; ".join(f"G {g:g} GPa: {m:.2f}x" for g, (m, _, _) in worst_by_g.items())
    return Verdict(
        "flutter.margin",
        "Fin flutter margin (Martin, NACA TN 4197, corrected)",
        PASS if margin >= f["min_margin"] else FAIL,
        f"{margin:.2f}x",
        f"≥ {f['min_margin']}x",
        f"{source} flight + Ogive flutter",
        f"worst at t = {t:.2f} s, {v:.0f} m/s. {detail}",
        assumptions,
        measured=margin,
    )


def ground_hit(velocity: float, rules: dict, source: str) -> Verdict:
    limit = rules["recovery"]["max_ground_hit"]
    return Verdict(
        "recovery.ground_hit",
        "Ground hit speed (total, as OpenRocket 24.12 reports it)",
        PASS if velocity <= limit else FAIL,
        f"{velocity:.2f} m/s",
        f"≤ {limit:g} m/s",
        source,
        measured=velocity,
    )


def deployment_warnings(warnings: Sequence[str], source: str) -> Verdict:
    fast = [w for w in warnings if "deployment at high speed" in w]
    return Verdict(
        "recovery.deployment",
        "Recovery deployment speed",
        WARN if fast else PASS,
        fast[0].split(":")[0] if fast else "no high-speed warning",
        "OpenRocket's own warning",
        source,
        "Threshold is the team's call; OpenRocket flags it as high" if fast else "",
    )


def motor_designation(
    designation: str, impulse: float, average_thrust: float, rules: dict, source: str
) -> Verdict:
    """The curve must deliver what its name says: '229H255' = 229 Ns total, 255 N average."""
    match = DESIGNATION.match(designation)
    if not match:
        return Verdict(
            "motor.designation",
            "Thrust curve matches its designation",
            PENDING,
            designation,
            "parseable designation",
            source,
            "can't read impulse/thrust from the name",
        )
    tol = rules["motor"]["designation_tolerance"]
    want_i, want_f = float(match["impulse"]), float(match["thrust"])
    err_i, err_f = impulse / want_i - 1, average_thrust / want_f - 1
    bad = abs(err_i) > tol or abs(err_f) > tol
    return Verdict(
        "motor.designation",
        "Thrust curve matches its designation",
        FAIL if bad else PASS,
        f"{impulse:.1f} Ns, {average_thrust:.0f} N",
        f"{want_i:g} Ns, {want_f:g} N within {tol:.0%}",
        source,
        f"impulse {err_i:+.1%}, average thrust {err_f:+.1%}"
        + (". This curve belongs to a different motor" if bad else ""),
    )


def stored_burn_time(
    designation: str,
    implied_burn: float | None,
    rules: dict,
    source: str,
    missing: str = "no stored summary",
) -> Verdict:
    """A stored sim's implied burn (apogee time - optimum delay) must match I/F.

    missing: why there is nothing to judge, shown when implied_burn is None.
    """
    match = DESIGNATION.match(designation)
    if implied_burn is None or not match:
        return Verdict(
            "stored.burn_time",
            "Stored sim burn time",
            PENDING,
            "—",
            "I / F",
            source,
            missing if match else "can't read impulse/thrust from the designation",
        )
    expected = float(match["impulse"]) / float(match["thrust"])
    tol = rules["motor"]["burn_time_tolerance_s"]
    return Verdict(
        "stored.burn_time",
        "Stored sim was run on this motor (burn time check)",
        PASS if abs(implied_burn - expected) <= tol else FAIL,
        f"{implied_burn:.3f} s",
        f"{expected:.3f} s ± {tol:g}",
        source,
        "time to apogee − optimum delay, vs I/F from the designation",
    )


def stored_matches_rerun(
    stored_apogee: float | None,
    stored_vmax: float | None,
    apogee: float,
    vmax: float,
    rules: dict,
    source: str,
    missing: str = "no stored summary",
) -> Verdict:
    if stored_apogee is None or stored_vmax is None:
        return Verdict(
            "stored.current",
            "Stored results match a fresh run",
            PENDING,
            "—",
            "—",
            source,
            missing,
        )
    tol = rules["stored"]["rerun_tolerance"]
    err = max(abs(stored_apogee / apogee - 1), abs(stored_vmax / vmax - 1))
    return Verdict(
        "stored.current",
        "Stored results match a fresh run",
        PASS if err <= tol else FAIL,
        f"off by {err:.1%}",
        f"≤ {tol:.0%}",
        source,
        f"stored {stored_apogee:.0f} m / {stored_vmax:.1f} m/s, "
        f"re-run {apogee:.0f} m / {vmax:.1f} m/s"
        + (
            ". The stored numbers are stale: they describe a different design or motor"
            if err > tol
            else ""
        ),
        measured=err,
    )


def rocket_equation(
    max_velocity: float,
    impulse: float,
    launch_mass: float,
    propellant_mass: float,
    burn_time: float,
    source: str,
) -> Verdict:
    """Hard bound: v_max ≤ I / m_burnout − g·t_burn. No backend may beat it with its own inputs."""
    ceiling = impulse / (launch_mass - propellant_mass) - G0 * burn_time
    return Verdict(
        "sanity.rocket_equation",
        "Speed below the rocket-equation ceiling",
        PASS if max_velocity <= ceiling else FAIL,
        f"{max_velocity:.1f} m/s",
        f"≤ {ceiling:.1f} m/s",
        source,
        "I / m_burnout − g·t_burn, from the motor the backend loaded",
        measured=max_velocity,
    )


def mass_consistency(dry: float, motor_launch: float, launch: float, source: str) -> Verdict:
    gap = launch - (dry + motor_launch)
    return Verdict(
        "sanity.mass",
        "Masses add up (dry + motor = loaded)",
        PASS if abs(gap) < 0.0005 else FAIL,
        f"{dry * 1000:.1f} + {motor_launch * 1000:.1f} g",
        f"= {launch * 1000:.1f} g",
        source,
    )


def fineness(length: float, diameter: float, rules: dict, source: str) -> Verdict:
    ratio, limit = length / diameter, rules["confidence"]["min_fineness"]
    return Verdict(
        "confidence.fineness",
        "Long enough for Barrowman stability to be trusted",
        PASS if ratio >= limit else WARN,
        f"L/D {ratio:.1f}",
        f"≥ {limit:g}",
        source,
        ""
        if ratio >= limit
        else "short, fat rockets: treat calibers with caution, check restoring moment",
        measured=ratio,
    )


def supersonic(max_mach: float, rules: dict, source: str) -> Verdict:
    limit = rules["confidence"]["supersonic_mach"]
    over = max_mach > limit
    return Verdict(
        "confidence.supersonic",
        "Speed regime OpenRocket models well",
        WARN if over else PASS,
        f"M{max_mach:.2f}",
        f"≤ M{limit:g}",
        source,
        "supersonic: cross-check drag and CP in RASAero II before flight (Lake 12)" if over else "",
        measured=max_mach,
    )


def mass_override_traps(traps: Sequence[str], source: str) -> Verdict:
    """traps: names of assemblies that override mass without covering their subcomponents."""
    return Verdict(
        "sanity.mass_override",
        "No mass-override traps",
        WARN if traps else PASS,
        ", ".join(traps) if traps else "none",
        "override covers subcomponents",
        source,
        "an override without 'override subcomponents' adds to the children's mass "
        "instead of replacing it"
        if traps
        else "",
    )
