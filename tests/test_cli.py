"""The ogive command line."""

from ogive.cli import main
from ogive.ork_io import OrkDocument


def test_tree_prints_components_and_unknown_freshness(v1_ork, capsys):
    assert main(["tree", str(v1_ork)]) == 0
    out = capsys.readouterr().out
    for name in ("Nose Cone", "Body Tube", "Trapezoidal Fin Set", "Nose Weight"):
        assert name in out
    assert "freshness UNKNOWN" in out
    assert "ISA" in out


def test_tree_shows_motor_curve_and_launch_temperature(v2_locked_ork, capsys):
    assert main(["tree", str(v2_locked_ork)]) == 0
    out = capsys.readouterr().out
    assert (
        "Motor: 229H255-14A (Cesaroni Technology) in Body Tube, delay 14 s, curve 39f13f3e" in out
    )
    assert "10.0 °C, 1013.25 hPa" in out
    assert "Parachute [parachute]" in out


def test_set_writes_a_copy_with_the_new_value(v1_ork, tmp_path, capsys):
    output = tmp_path / "v1_fin36.ork"
    args = ["set", str(v1_ork), "--component", "Trapezoidal Fin Set"]
    args += ["--field", "height", "--value", "0.036", "-o", str(output)]
    assert main(args) == 0
    assert "0.04 -> 0.036" in capsys.readouterr().out
    assert OrkDocument.load(output).find_component("Trapezoidal Fin Set").number("height") == 0.036


def test_set_refuses_to_overwrite_its_input(v1_ork, tmp_path, capsys):
    copy = tmp_path / "rocket.ork"
    copy.write_bytes(v1_ork.read_bytes())
    before = copy.read_bytes()
    args = ["set", str(copy), "--component", "Nose Cone", "--field", "length"]
    args += ["--value", "0.2", "-o", str(copy)]
    assert main(args) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert copy.read_bytes() == before


def test_set_unknown_component_is_a_clean_error(v1_ork, tmp_path, capsys):
    args = ["set", str(v1_ork), "--component", "Rudder", "--field", "height"]
    args += ["--value", "1", "-o", str(tmp_path / "x.ork")]
    assert main(args) == 1
    assert "no component named 'Rudder'" in capsys.readouterr().err
