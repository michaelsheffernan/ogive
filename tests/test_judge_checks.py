"""Judge checks on plain numbers. No OpenRocket needed."""

import pytest

from ogive.judge import FAIL, PASS, PENDING, WARN, checks, load_rules
from ogive.physics.flutter import Fin

RULES = load_rules()
SRC = "test"


@pytest.mark.parametrize(
    ("cal", "state"), [(1.4, FAIL), (1.5, WARN), (1.99, WARN), (2.0, PASS), (3.0, PASS)]
)
def test_static_stability_thresholds(cal, state):
    assert checks.static_stability(cal, RULES, SRC).state == state


def test_ascent_stability_judges_the_worst_sample():
    samples = [(0.1, 2.5, 0.2), (0.5, 1.4, 0.8), (1.0, 3.0, 1.2)]
    v = checks.ascent_stability(samples, RULES, SRC)
    assert v.state == FAIL and "1.40" in v.value and "t = 0.50" in v.detail


def test_ascent_stability_without_samples_is_pending():
    assert checks.ascent_stability([], RULES, SRC).state == PENDING


def test_rod_velocity():
    assert checks.rod_velocity(14.9, RULES, SRC).state == FAIL
    assert checks.rod_velocity(15.0, RULES, SRC).state == PASS


def test_flutter_uses_the_lowest_shear_modulus_and_flags_the_assumption():
    fin = Fin(span=0.032, root_chord=0.07, tip_chord=0.025, thickness=0.0025)
    samples = [(0.5, 400.0, 101325.0, 337.0), (0.9, 524.0, 100000.0, 337.0)]
    v = checks.flutter(fin, samples, RULES, SRC)
    low, high = v.detail.split("; ")[0], v.detail.split("; ")[-1]
    assert "G 3 GPa" in low and "G 17 GPa" in high
    assert v.value in v.detail  # the headline margin is the low-G one
    assert v.assumptions and "ASSUMED" in v.assumptions[0]


def test_flutter_with_a_measured_modulus_has_no_assumption():
    rules = load_rules()
    rules["flutter"] = {
        **rules["flutter"],
        "shear_modulus_gpa": [5.0],
        "shear_modulus_source": "datasheet",
    }
    fin = Fin(0.032, 0.07, 0.025, 0.0025)
    v = checks.flutter(fin, [(0.9, 524.0, 100000.0, 337.0)], rules, SRC)
    assert v.assumptions == ()


@pytest.mark.parametrize(
    ("impulse", "thrust", "state"),
    [(229.0, 257.0, PASS), (316.1, 264.0, FAIL), (229.0, 300.0, FAIL)],
)
def test_motor_designation(impulse, thrust, state):
    assert checks.motor_designation("229H255-14A", impulse, thrust, RULES, SRC).state == state


def test_unreadable_designation_is_pending():
    assert checks.motor_designation("Mystery", 100, 50, RULES, SRC).state == PENDING


def test_stored_burn_time_catches_the_misfiled_curve():
    assert checks.stored_burn_time("229H255-14A", 0.936, RULES, SRC).state == PASS
    assert checks.stored_burn_time("229H255-14A", 1.252, RULES, SRC).state == FAIL
    assert checks.stored_burn_time("229H255-14A", None, RULES, SRC).state == PENDING


def test_stored_matches_rerun():
    assert checks.stored_matches_rerun(1902.2, 524.2, 1902.0, 524.0, RULES, SRC).state == PASS
    stale = checks.stored_matches_rerun(2240.3, 576.1, 1923.0, 524.6, RULES, SRC)
    assert stale.state == FAIL and "stale" in stale.detail


def test_rocket_equation_hard_bound():
    # 229 Ns, 420 g loaded, 113 g propellant, 0.889 s burn: ceiling = 229 / 0.307 - 9.80665 * 0.889
    ceiling = 229 / (0.420 - 0.113) - 9.80665 * 0.889
    assert checks.rocket_equation(ceiling - 1, 229, 0.420, 0.113, 0.889, SRC).state == PASS
    assert checks.rocket_equation(ceiling + 1, 229, 0.420, 0.113, 0.889, SRC).state == FAIL


def test_mass_consistency():
    assert checks.mass_consistency(0.18845, 0.232, 0.42045, SRC).state == PASS
    assert checks.mass_consistency(0.18845, 0.232, 0.507, SRC).state == FAIL


def test_ground_hit_fineness_supersonic():
    assert checks.ground_hit(10.5, RULES, SRC).state == FAIL
    assert checks.fineness(0.30, 0.05, RULES, SRC).state == WARN  # L/D 6
    assert checks.supersonic(1.56, RULES, SRC).state == WARN
    assert checks.supersonic(0.9, RULES, SRC).state == PASS


def test_deployment_and_override_warnings():
    w = checks.deployment_warnings(
        ['Recovery device deployment at high speed (28.9 m/s):  "Parachute"'], SRC
    )
    assert w.state == WARN and "28.9" in w.value
    assert checks.mass_override_traps(["Payload bay"], SRC).state == WARN
    assert checks.mass_override_traps([], SRC).state == PASS


def test_rules_override_file(tmp_path):
    custom = tmp_path / "team.toml"
    custom.write_text("[stability]\ngoal_cal = 2.5\n", encoding="utf-8")
    rules = load_rules(custom)
    assert rules["stability"]["goal_cal"] == 2.5
    assert rules["stability"]["min_cal"] == 1.5  # untouched values keep their defaults


def test_outdated_stored_results_are_pending_not_failed():
    missing = "stored results are marked outdated"
    v = checks.stored_matches_rerun(None, None, 1900.0, 520.0, RULES, SRC, missing)
    assert v.state == PENDING and v.detail == missing
    w = checks.stored_burn_time("229H255", None, RULES, SRC, missing)
    assert w.state == PENDING and w.detail == missing


def test_numeric_checks_carry_the_raw_measurement():
    assert checks.static_stability(2.64, RULES, SRC).measured == 2.64
    assert checks.rod_velocity(46.7, RULES, SRC).measured == 46.7
    assert checks.supersonic(1.557, RULES, SRC).measured == 1.557
