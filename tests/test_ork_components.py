"""Component views, value parsing and stored simulations."""

import pytest

from ogive.ork_io import OrkDocument
from ogive.ork_io.components import FieldValue, parse_value


@pytest.fixture
def doc(v1_ork):
    return OrkDocument.load(v1_ork)


def test_component_tree_order(doc):
    tree = [(c.depth, c.kind, c.name) for c in doc.components()]
    assert tree == [
        (0, "rocket", "heloria_blackhawk29_v1.ork"),
        (1, "stage", "Sustainer"),
        (2, "nosecone", "Nose Cone"),
        (2, "bodytube", "Body Tube"),
        (3, "trapezoidfinset", "Trapezoidal Fin Set"),
        (3, "masscomponent", "Av-bay + electronics"),
        (3, "masscomponent", "Recovery"),
        (3, "masscomponent", "Tracker"),
        (3, "masscomponent", "Nose Weight"),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.04", FieldValue(0.04, auto=False)),
        ("auto 0.01525", FieldValue(0.01525, auto=True)),
        ("7.000000000000001E-4", FieldValue(7.000000000000001e-4, auto=False)),
    ],
)
def test_parse_value(text, expected):
    assert parse_value(text) == expected


@pytest.mark.parametrize("text", ["abc", "auto", "manual 0.1", "1 2 3"])
def test_parse_value_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_value(text)


def test_reads_auto_field(doc):
    assert doc.find_component("Nose Cone").get("aftradius") == FieldValue(0.01525, auto=True)


def test_set_turns_auto_off(doc):
    nose = doc.find_component("Nose Cone")
    nose.set("aftradius", 0.0153)
    assert nose.get("aftradius") == FieldValue(0.0153, auto=False)


def test_set_refuses_to_invent_fields(doc):
    with pytest.raises(KeyError):
        doc.find_component("Nose Cone").set("wingspan", 1.0)


def test_find_component_ignores_surrounding_whitespace(doc):
    # The file stores this name as "Recovery " with a trailing space.
    assert doc.find_component("Recovery").kind == "masscomponent"


def test_find_component_unknown_name_lists_known_names(doc):
    with pytest.raises(KeyError, match="Nose Cone"):
        doc.find_component("Rudder")


def test_material(doc):
    material = doc.find_component("Trapezoidal Fin Set").material()
    assert (material.name, material.density, material.type) == ("Carbon fiber", 1780.0, "bulk")


def test_summary_only_simulation_has_unknown_freshness(doc):
    # Saved without sim data, OpenRocket writes "notsimulated" whatever the real state.
    (sim,) = doc.simulations()
    assert sim.name == "Simulation 1"
    assert sim.status == "notsimulated"
    assert sim.freshness == "unknown"
    assert sim.conditions["launchrodlength"] == "1.0"
    assert sim.conditions["atmosphere"] == "isa"
    assert sim.flight_data["maxvelocity"] == 577.8
