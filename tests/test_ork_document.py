"""OrkDocument: load, save, round trip, edits."""

import gzip
import xml.etree.ElementTree as ET
import zipfile

import pytest

from ogive.ork_io import OrkDocument, OrkFormatError


def canonical(xml_bytes: bytes) -> str:
    """C14N form: equal strings mean the same XML document, ignoring formatting."""
    return ET.canonicalize(xml_bytes.decode("utf-8"))


def read_entry(path, name="rocket.ork") -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


def test_loads_reference_metadata(v1_ork):
    doc = OrkDocument.load(v1_ork)
    assert doc.format_version == "1.10"
    assert doc.creator == "OpenRocket 24.12"


def test_round_trip_is_canonically_identical(v1_ork, tmp_path):
    original = read_entry(v1_ork)
    saved = OrkDocument.load(v1_ork).save(tmp_path / "copy.ork")
    assert canonical(read_entry(saved)) == canonical(original)


def test_round_trip_twice_is_stable(v1_ork, tmp_path):
    first = OrkDocument.load(v1_ork).save(tmp_path / "a.ork")
    second = OrkDocument.load(first).save(tmp_path / "b.ork")
    assert read_entry(first) == read_entry(second)


def test_saved_file_is_a_zip_with_rocket_entry(v1_ork, tmp_path):
    saved = OrkDocument.load(v1_ork).save(tmp_path / "copy.ork")
    with zipfile.ZipFile(saved) as archive:
        assert archive.namelist() == ["rocket.ork"]


def test_extra_zip_entries_survive(v1_ork, tmp_path):
    decal = b"\x89PNG fake image bytes"
    with_decal = tmp_path / "decal.ork"
    with zipfile.ZipFile(with_decal, "w") as archive:
        archive.writestr("rocket.ork", read_entry(v1_ork))
        archive.writestr("decals/logo.png", decal)

    saved = OrkDocument.load(with_decal).save(tmp_path / "out.ork")
    assert read_entry(saved, "decals/logo.png") == decal


@pytest.mark.parametrize("wrap", [lambda xml: xml, gzip.compress], ids=["plain", "gzip"])
def test_loads_plain_and_gzip_xml(v1_ork, tmp_path, wrap):
    path = tmp_path / "old_style.ork"
    path.write_bytes(wrap(read_entry(v1_ork)))
    doc = OrkDocument.load(path)
    assert doc.find_component("Nose Cone").kind == "nosecone"


def test_rejects_non_openrocket_xml(tmp_path):
    path = tmp_path / "other.ork"
    path.write_bytes(b"<?xml version='1.0'?><svg></svg>")
    with pytest.raises(OrkFormatError):
        OrkDocument.load(path)


def test_rejects_broken_xml(tmp_path):
    path = tmp_path / "broken.ork"
    path.write_bytes(b"<openrocket><rocket>")
    with pytest.raises(OrkFormatError):
        OrkDocument.load(path)


def test_fin_edit_changes_only_fin_height(v1_ork, tmp_path):
    doc = OrkDocument.load(v1_ork)
    doc.find_component("Trapezoidal Fin Set").set("height", 0.036)
    saved = doc.save(tmp_path / "fin36.ork")

    reloaded = OrkDocument.load(saved)
    assert reloaded.find_component("Trapezoidal Fin Set").number("height") == 0.036

    # Expected: the original XML with exactly one text changed, built independently.
    expected = ET.fromstring(read_entry(v1_ork))
    expected.find(".//trapezoidfinset/height").text = "0.036"
    expected_bytes = ET.tostring(expected, encoding="utf-8", xml_declaration=True)
    assert canonical(read_entry(saved)) == canonical(expected_bytes)


def test_mark_simulations_outdated_marks_only_sims_with_data(v2_locked_ork):
    doc = OrkDocument.load(v2_locked_ork)
    with_data = [s for s in doc.simulations() if s.flight_data]
    assert with_data, "the locked v2 file stores summaries"
    assert doc.mark_simulations_outdated() == len(with_data)
    assert {s.status for s in doc.simulations() if s.flight_data} == {"outdated"}
    assert doc.mark_simulations_outdated() == 0  # already outdated


def test_add_mass_component_inside_the_nose(v2_locked_ork, tmp_path):
    doc = OrkDocument.load(v2_locked_ork)
    added = doc.add_mass_component("Nose Cone", "Nose weight", 0.03, 0.10, 0.02, 0.007)
    assert added.parent.name == "Nose Cone" and added.depth == added.parent.depth + 1

    reloaded = OrkDocument.load(doc.save(tmp_path / "nose.ork"))
    weight = reloaded.find_component("Nose weight")
    assert weight.kind == "masscomponent" and weight.parent.kind == "nosecone"
    assert weight.number("mass") == 0.03 and weight.number("packedlength") == 0.02
    assert weight.element.find("axialoffset").get("method") == "top"
    assert weight.text("masscomponenttype") == "masscomponent"


def test_add_mass_component_rejects_duplicates_and_bad_sizes(v2_locked_ork):
    doc = OrkDocument.load(v2_locked_ork)
    with pytest.raises(ValueError, match="already exists"):
        doc.add_mass_component("Nose Cone", "Nose Cone", 0.01, 0.1, 0.02, 0.007)
    with pytest.raises(ValueError, match="must be > 0"):
        doc.add_mass_component("Nose Cone", "Weight", 0.0, 0.1, 0.02, 0.007)
    with pytest.raises(KeyError):
        doc.add_mass_component("Rudder", "Weight", 0.01, 0.1, 0.02, 0.007)
