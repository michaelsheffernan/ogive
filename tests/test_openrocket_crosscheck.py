"""Cross-check against OpenRocket itself. Needs a local OpenRocket install and jpype1.

OpenRocket's own loader reads our files, and every dimension and mass Ogive reads must
match what OpenRocket reads. This is Lake 1's acceptance test: OpenRocket opens our output.
Skipped automatically where OpenRocket isn't installed.
"""

import pytest

from ogive.ork_io import OrkDocument
from ogive.ork_io.mass import CONTAINER_KINDS, component_mass, dry_mass
from ogive.sim import openrocket

pytestmark = pytest.mark.openrocket

ORK_KIND_TO_OPENROCKET = {
    "nosecone": "NoseCone",
    "bodytube": "BodyTube",
    "trapezoidfinset": "TrapezoidFinSet",
}


@pytest.fixture(params=["v1_ork", "v2_locked_ork"])
def frozen_ork(request, running_openrocket):
    return request.getfixturevalue(request.param)


def test_openrocket_opens_a_file_we_wrote(running_openrocket, v2_locked_ork, tmp_path):
    doc = OrkDocument.load(v2_locked_ork)
    doc.find_component("Trapezoidal Fin Set").set("height", 0.030)
    written = doc.save(tmp_path / "ogive_written.ork")

    loaded = openrocket.load(written)
    assert loaded.warnings == []
    assert loaded.component("Trapezoidal Fin Set").dimensions["height"] == pytest.approx(0.030)
    assert len(loaded.motors) == 1


def test_openrocket_reads_every_dimension_as_we_do(frozen_ork):
    theirs = openrocket.load(frozen_ork)
    for c in OrkDocument.load(frozen_ork).components():
        if c.kind not in ORK_KIND_TO_OPENROCKET:
            continue
        data = theirs.component(c.name)
        assert data.kind == ORK_KIND_TO_OPENROCKET[c.kind]
        for tag, value in data.dimensions.items():
            assert c.number(tag) == pytest.approx(value), f"{c.name} <{tag}>"


def test_every_component_mass_matches_openrocket(frozen_ork):
    theirs = openrocket.load(frozen_ork)
    for c in OrkDocument.load(frozen_ork).components():
        if c.kind in CONTAINER_KINDS:
            continue
        expected = theirs.component(c.name).mass
        assert component_mass(c) == pytest.approx(expected, rel=0.002, abs=1e-5), c.name


def test_dry_mass_matches_openrocket(frozen_ork):
    summary = dry_mass(OrkDocument.load(frozen_ork).components())
    assert summary.unmodelled == []
    assert summary.total == pytest.approx(openrocket.load(frozen_ork).dry_mass, rel=0.001)


@pytest.mark.parametrize(
    ("fixture", "digest", "impulse_ns", "launch_kg"),
    [
        ("v1_ork", "6e3ee79bc9cde6345bf6d643473203f9", 316.1, 0.318),  # ThrustCurve's wrong file
        ("v2_locked_ork", "39f13f3e11a555a3edeb7a7079065b77", 229.0, 0.232),  # correct curve
    ],
)
def test_motor_curve_data_from_openrocket(
    running_openrocket, request, fixture, digest, impulse_ns, launch_kg
):
    (motor,) = openrocket.load(request.getfixturevalue(fixture)).motors
    assert motor.designation == "229H255-14A"
    assert motor.digest == digest
    assert motor.total_impulse == pytest.approx(impulse_ns, abs=0.1)
    assert motor.launch_mass == pytest.approx(launch_kg)


# Static stability, computed the way OpenRocket's design view does (RocketPanel.updateExtras).
# Expected values are the team lead's readings from the OpenRocket GUI, an independent path.
@pytest.mark.parametrize(
    ("fixture", "cg_m", "cp_m", "calibers"),
    [
        ("v1_ork", 0.598, 0.658, 2.0),  # GUI: CG 59.8 cm, CP 65.8 cm, 2.0 cal
        ("v2_locked_ork", None, None, 2.64),  # GUI: 2.64 cal
    ],
)
def test_static_stability_matches_openrocket_gui(
    running_openrocket, request, fixture, cg_m, cp_m, calibers
):
    analysis = openrocket.static_analysis(request.getfixturevalue(fixture))
    assert analysis.calibers == pytest.approx(calibers, abs=0.01)
    if cg_m is not None:
        assert analysis.cg_x == pytest.approx(cg_m, abs=0.001)
        assert analysis.cp_x == pytest.approx(cp_m, abs=0.001)


def test_cone_nose_cp_is_two_thirds_of_its_length(running_openrocket, v2_locked_ork):
    # Barrowman, textbook: a conical nose has CN-alpha = 2 and its CP at 2/3 of its length.
    nose = next(
        c for c in openrocket.static_analysis(v2_locked_ork).components if c.name == "Nose Cone"
    )
    assert nose.cp_x == pytest.approx(2 / 3 * 0.183)
    assert nose.cn_alpha == pytest.approx(2.0)


def test_burnout_cg_is_forward_of_launch_cg(frozen_ork):
    # Burning propellant at the tail moves the CG forward, so stability rises at burnout.
    analysis = openrocket.static_analysis(frozen_ork)
    assert analysis.cg_burnout_x < analysis.cg_x
    assert analysis.burnout_calibers > analysis.calibers


# ---------- Lake 2: running simulations ----------

REF_SIM = "Para + Streamer Test"


def same_result(a, b) -> bool:
    """FlightResult equality that treats NaN == NaN (OpenRocket emits NaN before launch)."""
    if (a.summary, a.conditions, a.events, a.warnings) != (
        b.summary,
        b.conditions,
        b.events,
        b.warnings,
    ):
        return False
    for key in a.series:
        for x, y in zip(a.series[key], b.series[key], strict=True):
            if x != y and not (x != x and y != y):
                return False
    return True


def test_same_seed_reproduces_across_fresh_loads(running_openrocket, v2_locked_ork):
    # OpenRocket 24.12 ignores setRandomSeed for wind. Ogive rebuilds the wind model (_set_seed).
    a = openrocket.simulate(v2_locked_ork, REF_SIM, seed=42)
    b = openrocket.simulate(v2_locked_ork, REF_SIM, seed=42)
    assert a.seed == b.seed == 42
    assert same_result(a, b)


def test_different_seeds_change_the_wind_only_slightly(running_openrocket, v2_locked_ork):
    a = openrocket.simulate(v2_locked_ork, REF_SIM, seed=1)
    b = openrocket.simulate(v2_locked_ork, REF_SIM, seed=2)
    assert a.series["wind"] != b.series["wind"]
    assert a.summary.apogee == pytest.approx(b.summary.apogee, rel=0.01)


@pytest.mark.parametrize("sim_name", ["Simulation 1", REF_SIM])
def test_rerun_matches_the_stored_summary(running_openrocket, v2_locked_ork, sim_name):
    # Truth Rule gate: a re-run must land within 5 % of the stored OpenRocket summary.
    stored = next(s for s in OrkDocument.load(v2_locked_ork).simulations() if s.name == sim_name)
    result = openrocket.simulate(v2_locked_ork, sim_name)
    s = result.summary
    assert s.apogee == pytest.approx(stored.flight_data["maxaltitude"], rel=0.05)
    assert s.max_velocity == pytest.approx(stored.flight_data["maxvelocity"], rel=0.05)
    assert s.max_mach == pytest.approx(stored.flight_data["maxmach"], rel=0.05)
    assert s.ground_hit_velocity == pytest.approx(stored.flight_data["groundhitvelocity"], rel=0.05)


def test_reference_sim_conditions_are_cork_10c_1p5m(running_openrocket, v2_locked_ork):
    c = openrocket.simulate(v2_locked_ork, REF_SIM, seed=0).conditions
    assert (c.rod_length, c.latitude, c.isa) == (1.5, 51.9, False)
    assert c.temperature == pytest.approx(283.15)
    assert c.longitude == 8.5  # known bug in the frozen file, should be -8.5


def test_series_are_aligned_and_agree_with_the_summary(running_openrocket, v2_locked_ork):
    r = openrocket.simulate(v2_locked_ork, REF_SIM, seed=42)
    lengths = {len(v) for v in r.series.values()}
    assert len(lengths) == 1 and lengths.pop() > 100
    assert max(r.series["altitude"]) == pytest.approx(r.summary.apogee)
    assert max(r.series["velocity"]) == pytest.approx(r.summary.max_velocity)
    assert r.event_time("Apogee") == pytest.approx(r.summary.time_to_apogee, abs=0.06)
    assert r.event_time("Motor burnout") == pytest.approx(0.936, abs=0.01)
    assert r.series["time"] == sorted(r.series["time"])


def test_unknown_simulation_name_lists_the_known_ones(running_openrocket, v2_locked_ork):
    with pytest.raises(KeyError, match=r"Para \+ Streamer Test"):
        openrocket.simulate(v2_locked_ork, "Nope")


def test_ogive_written_design_simulates(running_openrocket, v2_locked_ork, tmp_path):
    # The first real simulation of a design Ogive wrote: fins 32 -> 30 mm.
    doc = OrkDocument.load(v2_locked_ork)
    doc.find_component("Trapezoidal Fin Set").set("height", 0.030)
    draft = doc.save(tmp_path / "fin30.ork")
    base = openrocket.simulate(v2_locked_ork, REF_SIM, seed=42)
    trimmed = openrocket.simulate(draft, REF_SIM, seed=42)
    # Same warning kinds (the texts embed speeds that differ by 0.1 m/s).
    assert [w.split("(")[0] for w in trimmed.warnings] == [w.split("(")[0] for w in base.warnings]
    assert trimmed.summary.apogee == pytest.approx(base.summary.apogee, rel=0.02)
    # Stability only means something while flying nose-first with real airspeed.
    assert min(stability_in_flight(trimmed)) > 1.5


def stability_in_flight(result) -> list[float]:
    """Stability samples from rod clearance to the ejection charge, while Mach > 0.1.

    Near apogee the airspeed goes to zero and Barrowman's margin collapses; after ejection the
    rocket is no longer one body. Neither tells you anything about the ascent.
    """
    t0 = result.event_time("Launch rod clearance")
    t1 = result.event_time("Ejection charge") or result.event_time("Apogee")
    rows = zip(
        result.series["time"], result.series["stability"], result.series["mach"], strict=True
    )
    return [s for t, s, m in rows if t0 <= t <= t1 and m > 0.1 and s == s]


def test_v2_stability_through_the_ascent(running_openrocket, v2_locked_ork):
    r = openrocket.simulate(v2_locked_ork, REF_SIM, seed=42)
    rows = zip(r.series["time"], r.series["stability"], r.series["mach"], strict=True)
    at_rod = next(s for t, s, m in rows if t >= r.event_time("Launch rod clearance"))
    assert at_rod == pytest.approx(2.0, abs=0.05)  # The team lead's GUI reading: ~2 cal at rod exit
    assert min(stability_in_flight(r)) > 1.9
    assert max(stability_in_flight(r)) == pytest.approx(
        4.9, abs=0.1
    )  # The team lead: 4 to 4.5 in coast


# ---------- Judge regression set (frozen files in data/regression/) ----------


@pytest.mark.parametrize(
    ("fixture", "sim_name", "passed", "must_fail"),
    [
        ("v2_locked_ork", REF_SIM, True, set()),
        (
            "v1_ork",
            "Simulation 1",
            False,
            {"motor.designation", "stored.burn_time", "stability.ascent"},
        ),
        ("v2_locked_ork", "Streamer SIM", False, {"stored.current", "stored.burn_time"}),
        ("v2_locked_ork", "Parachute SIM", False, {"stored.current", "stored.burn_time"}),
        ("v2_locked_ork", "Simulation 1", True, set()),
    ],
)
def test_judge_regression_set(running_openrocket, request, fixture, sim_name, passed, must_fail):
    from ogive.judge import FAIL, judge_file

    report = judge_file(request.getfixturevalue(fixture), sim_name, seed=0)
    failed = {v.rule for v in report.verdicts if v.state == FAIL}
    assert report.passed is passed
    assert must_fail <= failed


def test_judge_refuses_fins_too_small(running_openrocket, v2_locked_ork, tmp_path):
    from ogive.judge import FAIL, judge_file

    doc = OrkDocument.load(v2_locked_ork)
    doc.find_component("Trapezoidal Fin Set").set("height", 0.020)
    report = judge_file(doc.save(tmp_path / "fin20.ork"), REF_SIM, seed=0)
    assert report.verdict("stability.static").state == FAIL
    assert report.verdict("stability.ascent").state == FAIL


def test_cli_judge_exit_codes(running_openrocket, v1_ork, v2_locked_ork, capsys):
    from ogive.cli import main

    assert main(["judge", str(v2_locked_ork), "--sim", REF_SIM]) == 0
    assert main(["judge", str(v1_ork)]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "ASSUMPTION" in out


def test_nose_weight_mass_and_cg_match_openrocket(running_openrocket, v2_locked_ork, tmp_path):
    from ogive.opt.sweep import NoseWeight, Variant, build_variant

    base = openrocket.static_analysis(v2_locked_ork)
    nose = NoseWeight()
    path = build_variant(v2_locked_ork, Variant(0.032, 0.030), tmp_path / "nw.ork", nose)
    heavy = openrocket.static_analysis(path)
    assert heavy.mass_without_motors - base.mass_without_motors == pytest.approx(0.030, abs=1e-9)
    # CG moves to the mass-weighted mean of the old CG and the slug's centre.
    slug_cg = nose.top_offset + nose.length / 2
    m = base.mass_with_motors
    expected = (m * base.cg_x + 0.030 * slug_cg) / (m + 0.030)
    assert heavy.cg_x == pytest.approx(expected, abs=1e-6)
    assert heavy.calibers > base.calibers


def test_unchanged_variant_reproduces_the_reference(running_openrocket, v2_locked_ork, tmp_path):
    from ogive.judge import PENDING, judge_file
    from ogive.opt.sweep import Variant, build_variant

    direct = judge_file(v2_locked_ork, REF_SIM, seed=0)
    path = build_variant(v2_locked_ork, Variant(0.032, 0.0), tmp_path / "same.ork")
    variant = judge_file(path, REF_SIM, seed=0)
    assert variant.metrics == direct.metrics
    assert variant.passed
    stored = variant.verdict("stored.current")
    assert stored.state == PENDING and "outdated" in stored.detail


def test_cli_optimize_small_grid(running_openrocket, v2_locked_ork, tmp_path, capsys):
    from ogive.cli import main

    args = ["optimize", str(v2_locked_ork), "--sim", REF_SIM, "-o", str(tmp_path)]
    assert main([*args, "--fins", "20:32:12", "--nose", "0:30:30"]) == 0
    out = capsys.readouterr().out
    assert "Lightest passing:       fins   32 mm, nose   0 g  PASS" in out
    lines = (tmp_path / "results.csv").read_text(encoding="utf-8").splitlines()
    verdicts = {(c[0], c[1]): c[2] for c in (line.split(",") for line in lines[1:])}
    assert verdicts == {
        ("20", "0"): "FAIL",
        ("20", "30"): "FAIL",
        ("32", "0"): "PASS",
        ("32", "30"): "PASS",
    }
    assert (tmp_path / "pareto.svg").exists()
    assert len(list((tmp_path / "variants").glob("*.ork"))) == 4

    assert main([*args, "--fins", "20", "--nose", "0"]) == 1  # nothing passes
