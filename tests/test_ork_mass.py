"""ork_io.mass: the wiring between .ork components and the geometry formulas.

The geometry functions are replaced with fakes here, so these tests check the wiring
(which fields go where, density, fin count) independently of the formulas themselves.
"""

import xml.etree.ElementTree as ET

import pytest

from ogive.ork_io import OrkDocument
from ogive.ork_io.mass import component_mass, dry_mass
from ogive.physics import geometry


@pytest.fixture
def doc(v1_ork):
    return OrkDocument.load(v1_ork)


@pytest.fixture
def fake_geometry(monkeypatch):
    """Each fake records its arguments and returns 1e-6 (1 cm^3 volume, or 1 mm^2 fillet area)."""
    calls = {}

    def fake(name):
        def record(*args):
            calls[name] = args
            return 1e-6

        return record

    for name in ("tube_volume", "cone_shell_volume", "trapezoid_fin_volume", "fin_fillet_area"):
        monkeypatch.setattr(geometry, name, fake(name))
    return calls


def test_mass_component_uses_stored_mass(doc):
    assert component_mass(doc.find_component("Nose Weight")) == 0.01


def test_override_mass_wins(doc):
    tube = doc.find_component("Body Tube")
    ET.SubElement(tube.element, "overridemass").text = "0.123"
    assert component_mass(tube) == 0.123


def test_unmodelled_nose_shape_returns_none(doc):
    nose = doc.find_component("Nose Cone")
    nose.element.find("shape").text = "ogive"
    assert component_mass(nose) is None


def test_body_tube_wiring(doc, fake_geometry):
    mass = component_mass(doc.find_component("Body Tube"))
    assert fake_geometry["tube_volume"] == (0.01525, 7.000000000000001e-4, 0.61)
    assert mass == pytest.approx(1e-6 * 1850.0)


def test_nose_cone_wiring(doc, fake_geometry):
    mass = component_mass(doc.find_component("Nose Cone"))
    assert fake_geometry["cone_shell_volume"] == (0.01525, 0.183, 0.001)
    assert mass == pytest.approx(1e-6 * 1780.0)


# Per fin: plate 1e-6 m^3 x 0.99 (rounded section) x 1780 kg/m^3 carbon,
# plus fillet 1e-6 m^2 x 0.07 m root chord x 170 kg/m^3 balsa.
ONE_FIN_FAKE = 1e-6 * 0.99 * 1780.0 + 1e-6 * 0.07 * 170.0


def test_fin_set_wiring_counts_every_fin(doc, fake_geometry):
    mass = component_mass(doc.find_component("Trapezoidal Fin Set"))
    assert fake_geometry["trapezoid_fin_volume"] == (0.07, 0.025, 0.04, 0.0025)
    assert fake_geometry["fin_fillet_area"] == (0.0025, 0.01525)  # fillet radius, tube radius
    assert mass == pytest.approx(ONE_FIN_FAKE * 3)


def test_fin_tab_is_not_guessed(doc):
    fins = doc.find_component("Trapezoidal Fin Set")
    ET.SubElement(fins.element, "tabheight").text = "0.005"
    assert component_mass(fins) is None


def test_unknown_fin_cross_section_is_not_guessed(doc):
    fins = doc.find_component("Trapezoidal Fin Set")
    fins.element.find("crosssection").text = "wedge"
    assert component_mass(fins) is None


def test_nose_shoulder_is_not_guessed(doc):
    nose = doc.find_component("Nose Cone")
    nose.element.find("aftshoulderlength").text = "0.02"
    assert component_mass(nose) is None


def test_dry_mass_sums_parts_and_skips_containers(doc, fake_geometry):
    summary = dry_mass(doc.components())
    structural = 1e-6 * (1780.0 + 1850.0) + ONE_FIN_FAKE * 3  # nose, tube, 3 fins
    mass_components = 0.03 + 0.015 + 0.02 + 0.01
    assert summary.total == pytest.approx(structural + mass_components)
    assert summary.unmodelled == []


# Recovery devices: hand-worked from the frozen v2 file's fields.


def test_parachute_mass_is_canopy_plus_lines(v2_locked_ork):
    chute = OrkDocument.load(v2_locked_ork).find_component("Parachute")
    # Canopy: pi * (0.3 / 2)^2 m^2 * 0.067 kg/m^2 = 4.736 g.
    # Lines: 6 * 0.3 m * 0.0018 kg/m = 3.24 g.
    assert component_mass(chute) == pytest.approx(0.004736 + 0.00324, rel=1e-3)


def test_streamer_mass_is_strip_area_times_fabric(v2_locked_ork):
    streamer = OrkDocument.load(v2_locked_ork).find_component("Streamer")
    # 1.0 m * 0.1 m * 0.067 kg/m^2 = 6.7 g
    assert component_mass(streamer) == pytest.approx(0.0067)


def test_parachute_without_line_material_is_not_guessed(v2_locked_ork):
    chute = OrkDocument.load(v2_locked_ork).find_component("Parachute")
    chute.element.remove(chute.element.find("linematerial"))
    assert component_mass(chute) is None


def test_every_v2_component_is_modelled(v2_locked_ork):
    assert dry_mass(OrkDocument.load(v2_locked_ork).components()).unmodelled == []
