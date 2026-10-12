"""Optimizer search and ranking on plain data. No OpenRocket needed."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ogive.judge import FAIL, PASS, WARN, Report, Verdict
from ogive.opt import output
from ogive.opt.sweep import (
    NOSE_WEIGHT_NAME,
    Row,
    Variant,
    build_variant,
    frange,
    front_rows,
    lightest,
    pareto_front,
)
from ogive.ork_io import OrkDocument


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((30, 45, 5), [30, 35, 40, 45]),
        ((0, 30, 7), [0, 7, 14, 21, 28]),
        ((32, 32, 1), [32]),
        ((0.1, 0.3, 0.1), [0.1, 0.2, 0.3]),
    ],
)
def test_frange_is_inclusive_without_float_drift(args, expected):
    assert frange(*args) == expected


@pytest.mark.parametrize("args", [(0, 10, 0), (10, 0, 1), (0, 10, -1)])
def test_frange_rejects_bad_ranges(args):
    with pytest.raises(ValueError):
        frange(*args)


def test_variant_label_names_the_file():
    assert Variant(0.032, 0.0).label == "fin32mm_nose0g"
    assert Variant(0.0325, 0.015).label == "fin32.5mm_nose15g"


def test_pareto_front_keeps_only_unbeaten_points():
    points = [(2.0, 1.55), (3.0, 1.46), (2.5, 1.50), (2.4, 1.45), (2.0, 1.40)]
    assert pareto_front(points) == [0, 1, 2]


def test_pareto_front_keeps_ties():
    assert pareto_front([(1.0, 1.0), (1.0, 1.0)]) == [0, 1]


def row(height_mm, nose_g, mass_g, apogee, mach, stab, state=PASS, stab_state=PASS) -> Row:
    verdicts = [
        Verdict("stability.ascent", "", stab_state, "", "", "", measured=stab),
        Verdict("launch.rod_velocity", "", state, "", "", ""),
    ]
    metrics = {"launch_mass": mass_g / 1000, "apogee": apogee, "max_mach": mach}
    report = Report("f.ork", "sim", 0, "test", verdicts, metrics)
    return Row(Variant(height_mm / 1000, nose_g / 1000), Path("f.ork"), report)


def test_lightest_passing_and_lightest_meeting_goals():
    rows = [
        row(28, 0, 410, 1950, 1.60, 1.2, state=FAIL, stab_state=FAIL),
        row(30, 0, 415, 1920, 1.58, 1.8, stab_state=WARN),
        row(32, 0, 420, 1902, 1.56, 2.0),
        row(32, 5, 425, 1910, 1.54, 2.2),
    ]
    assert lightest(rows).variant == Variant(0.030, 0.0)
    assert lightest(rows, goals=True).variant == Variant(0.032, 0.0)
    assert rows[0].failures == ["stability.ascent", "launch.rod_velocity"]


def test_lightest_breaks_mass_ties_on_apogee():
    rows = [row(30, 0, 415, 1900, 1.5, 2.1), row(31, 0, 415, 1950, 1.5, 2.1)]
    assert lightest(rows).variant.fin_height == 0.031


def test_lightest_is_none_when_nothing_passes():
    assert lightest([row(28, 0, 410, 1950, 1.6, 1.2, state=FAIL)]) is None


def test_front_rows_ignore_failed_designs():
    rows = [
        row(28, 0, 410, 1950, 1.70, 2.5, state=FAIL),  # would dominate, but fails
        row(32, 0, 420, 1902, 1.56, 2.0),
        row(32, 30, 450, 1928, 1.46, 2.9),
    ]
    assert [r.variant.nose_mass for r in front_rows(rows)] == [0.0, 0.03]


def test_build_variant_changes_fins_adds_weight_and_outdates_sims(v2_locked_ork, tmp_path):
    path = build_variant(v2_locked_ork, Variant(0.036, 0.015), tmp_path / "v.ork")
    doc = OrkDocument.load(path)
    assert doc.find_component("Trapezoidal Fin Set").number("height") == 0.036
    weight = doc.find_component(NOSE_WEIGHT_NAME)
    assert weight.number("mass") == 0.015 and weight.parent.kind == "nosecone"
    assert {s.status for s in doc.simulations() if s.flight_data} == {"outdated"}


def test_build_variant_without_nose_weight_adds_nothing(v2_locked_ork, tmp_path):
    before = len(OrkDocument.load(v2_locked_ork).components())
    path = build_variant(v2_locked_ork, Variant(0.032, 0.0), tmp_path / "v.ork")
    assert len(OrkDocument.load(path).components()) == before


def test_csv_and_svg_outputs(tmp_path):
    rows = [
        row(28, 0, 410, 1950, 1.60, 1.2, state=FAIL, stab_state=FAIL),
        row(32, 0, 420, 1902, 1.56, 2.0),
        row(32, 30, 450, 1928, 1.46, 2.9),
    ]
    front = front_rows(rows)
    path = output.write_csv(rows, front, tmp_path / "results.csv")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",") == output.CSV_COLUMNS and len(lines) == 4
    assert lines[1].split(",")[2] == "FAIL" and lines[2].split(",")[4] == "yes"

    svg = output.pareto_svg(rows, front, {"lightest passing": rows[1]}, 1.5, 2.0, "test & co")
    root = ET.fromstring(svg)  # well-formed, and the subtitle is escaped
    assert root.tag.endswith("svg")
    assert ">lightest passing<" in svg and ">32 mm fins, 0 g nose<" in svg
    assert svg.count("<polyline") == 1
