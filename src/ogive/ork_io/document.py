"""Read and write OpenRocket .ork files.

An .ork is normally a zip holding one XML file (rocket.ork), sometimes plus decal images.
Older files can be gzipped or plain XML. We always save a zip and keep other entries as-is.

The XML tree is the source of truth. Edits change it in place, so everything we don't
model survives a round trip untouched.
"""

from __future__ import annotations

import gzip
import io
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ogive.ork_io.components import Component, MotorRef, iter_components, motors_in
from ogive.ork_io.simulations import OUTDATED, StoredSimulation

DEFAULT_ENTRY = "rocket.ork"
GZIP_MAGIC = b"\x1f\x8b"


class OrkFormatError(ValueError):
    """The file is not an OpenRocket document we can read."""


@dataclass
class OrkDocument:
    root: ET.Element
    entry_name: str = DEFAULT_ENTRY
    extra_entries: dict[str, bytes] = field(default_factory=dict)  # decals etc., kept as-is

    @classmethod
    def load(cls, path: str | Path) -> OrkDocument:
        data = Path(path).read_bytes()
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                entry = _rocket_entry(names)
                extra = {name: archive.read(name) for name in names if name != entry}
                return cls(_parse(archive.read(entry)), entry, extra)
        if data.startswith(GZIP_MAGIC):
            return cls(_parse(gzip.decompress(data)))
        return cls(_parse(data))

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(self.entry_name, self.to_xml_bytes())
            for name, blob in self.extra_entries.items():
                archive.writestr(name, blob)
        return path

    def to_xml_bytes(self) -> bytes:
        return ET.tostring(self.root, encoding="utf-8", xml_declaration=True)

    @property
    def format_version(self) -> str:
        return self.root.get("version", "")

    @property
    def creator(self) -> str:
        return self.root.get("creator", "")

    def components(self) -> list[Component]:
        rocket = self.root.find("rocket")
        if rocket is None:
            raise OrkFormatError("no <rocket> element")
        return list(iter_components(rocket))

    def find_component(self, name: str) -> Component:
        """Find one component by name. Surrounding whitespace is ignored."""
        matches = [c for c in self.components() if c.name == name.strip()]
        if not matches:
            known = ", ".join(repr(c.name) for c in self.components())
            raise KeyError(f"no component named {name!r}. Known: {known}")
        if len(matches) > 1:
            raise KeyError(f"{len(matches)} components are named {name!r}; names must be unique")
        return matches[0]

    def motors(self) -> list[MotorRef]:
        return [motor for c in self.components() for motor in motors_in(c)]

    def simulations(self) -> list[StoredSimulation]:
        return [
            StoredSimulation.from_element(e) for e in self.root.iterfind("simulations/simulation")
        ]

    def mark_simulations_outdated(self) -> int:
        """Mark every stored result as outdated, as OpenRocket does when the design changes.

        Call after any edit: the stored numbers describe the design before it. OpenRocket's
        loader keeps "outdated" for a sim that has data. Returns how many sims were marked.
        """
        marked = 0
        for sim in self.root.iterfind("simulations/simulation"):
            if sim.find("flightdata") is not None and sim.get("status") != OUTDATED:
                sim.set("status", OUTDATED)
                marked += 1
        return marked

    def add_mass_component(
        self, parent: str, name: str, mass: float, top_offset: float, length: float, radius: float
    ) -> Component:
        """Add a mass component (e.g. nose weight) inside `parent`, written like OpenRocket's saver.

        Units are SI. top_offset: from the parent's front end to the front of the mass, so its
        centre of mass sits at top_offset + length / 2 behind the parent's front.
        """
        if any(c.name == name for c in self.components()):
            raise ValueError(f"a component named {name!r} already exists")
        if mass <= 0 or length <= 0 or radius <= 0 or top_offset < 0:
            raise ValueError("mass, length and radius must be > 0 and top_offset >= 0")
        host = self.find_component(parent)
        children = host.element.find("subcomponents")
        if children is None:
            children = ET.SubElement(host.element, "subcomponents")
        element = ET.SubElement(children, "masscomponent")
        fields = [
            ("name", name, {}),
            ("id", str(uuid.uuid4()), {}),
            ("axialoffset", repr(top_offset), {"method": "top"}),
            ("position", repr(top_offset), {"type": "top"}),
            ("packedlength", repr(length), {}),
            ("packedradius", repr(radius), {}),
            ("radialposition", "0.0", {}),
            ("radialdirection", "0.0", {}),
            ("mass", repr(mass), {}),
            ("masscomponenttype", "masscomponent", {}),
        ]
        for tag, text, attributes in fields:
            ET.SubElement(element, tag, attributes).text = text
        return Component(element, host.depth + 1, host)


def _rocket_entry(names: list[str]) -> str:
    if DEFAULT_ENTRY in names:
        return DEFAULT_ENTRY
    for name in names:
        if name.endswith(".ork"):
            return name
    raise OrkFormatError(f"zip has no .ork entry: {names}")


def _parse(xml_bytes: bytes) -> ET.Element:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    try:
        root = ET.fromstring(xml_bytes, parser=parser)
    except ET.ParseError as error:
        raise OrkFormatError(f"not valid XML: {error}") from error
    if root.tag != "openrocket":
        raise OrkFormatError(f"root element is <{root.tag}>, expected <openrocket>")
    return root
