"""The reference rocket (v2) and the frozen regression files."""

import hashlib
import xml.etree.ElementTree as ET
import zipfile

import pytest

from ogive.ork_io import OrkDocument
from ogive.ork_io.simulations import StoredSimulation

GOOD_CURVE = "39f13f3e11a555a3edeb7a7079065b77"  # 229H255-14A, ~229 Ns
BAD_CURVE = "6e3ee79bc9cde6345bf6d643473203f9"  # labelled 229H255-14A, 316 Ns (case study)

FROZEN = {
    "heloria_blackhawk29_v1.ork": (
        "c5aec7642f3fed385b258e3288de68325bd686897d54e4d49f02d25ea0c8c0cc"
    ),
    "blackhawk29_v2_locked.ork": "a32d2d95e730fb957d5d18b91231acdc0dddea5b8003d840de5e00e0b707c307",
}


@pytest.mark.parametrize(("name", "sha256"), FROZEN.items())
def test_regression_files_are_frozen(v1_ork, name, sha256):
    path = v1_ork.parent / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256, f"{name} was modified"


def test_reference_is_the_locked_v2_design(reference_ork):
    doc = OrkDocument.load(reference_ork)
    assert doc.find_component("Trapezoidal Fin Set").number("height") == 0.032
    assert "Nose Weight" not in [c.name for c in doc.components()]
    (motor,) = doc.motors()
    assert (motor.designation, motor.digest) == ("229H255-14A", GOOD_CURVE)


def test_v1_uses_the_bad_curve(v1_ork):
    (motor,) = OrkDocument.load(v1_ork).motors()
    assert (motor.designation, motor.digest) == ("229H255-14A", BAD_CURVE)


def test_v2_locked_sims_and_cork_conditions(v2_locked_ork):
    sims = {s.name: s for s in OrkDocument.load(v2_locked_ork).simulations()}
    assert list(sims) == ["Simulation 1", "Streamer SIM", "Parachute SIM", "Para + Streamer Test"]
    cork = sims["Para + Streamer Test"]
    assert cork.conditions["launchrodlength"] == "1.5"
    assert cork.conditions["atmosphere"] == "extendedisa"
    assert cork.conditions["atmosphere.basetemperature"] == "283.15"  # 10 C
    assert cork.conditions["launchlatitude"] == "51.9"
    # Known bug in the locked file: should be -8.5 (west). Frozen as-is on purpose.
    assert cork.conditions["launchlongitude"] == "8.5"
    assert cork.flight_data["maxaltitude"] == 1902.212


def test_v2_locked_round_trip_is_canonically_identical(v2_locked_ork, tmp_path):
    def entry(path):
        with zipfile.ZipFile(path) as archive:
            return ET.canonicalize(archive.read("rocket.ork").decode("utf-8"))

    saved = OrkDocument.load(v2_locked_ork).save(tmp_path / "copy.ork")
    assert entry(saved) == entry(v2_locked_ork)


@pytest.mark.parametrize(
    ("status", "flight_data", "expected"),
    [
        ("uptodate", {"maxaltitude": 1.0}, "current"),
        ("outdated", {"maxaltitude": 1.0}, "outdated"),
        ("notsimulated", {"maxaltitude": 1.0}, "unknown"),
        ("notsimulated", {}, "never run"),
        ("cantrun", {}, "unknown"),
    ],
)
def test_freshness(status, flight_data, expected):
    sim = StoredSimulation("s", status, conditions={}, flight_data=flight_data)
    assert sim.freshness == expected
