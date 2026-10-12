"""Typed views over the component elements of an .ork XML tree.

A Component wraps one XML element. Reading a field parses its text; writing a field
changes the XML in place. Units are SI, exactly as stored in the file (m, kg, kg/m^3).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass

AUTO_PREFIX = "auto"


@dataclass(frozen=True)
class FieldValue:
    """A numeric field. auto=True means OpenRocket computes it (stored as 'auto 0.01525')."""

    value: float
    auto: bool


def parse_value(text: str) -> FieldValue:
    """Parse '0.04' or 'auto 0.01525'."""
    parts = text.split()
    if len(parts) == 1:
        return FieldValue(float(parts[0]), auto=False)
    if len(parts) == 2 and parts[0] == AUTO_PREFIX:
        return FieldValue(float(parts[1]), auto=True)
    raise ValueError(f"expected a number or 'auto <number>', got {text!r}")


@dataclass(frozen=True)
class Material:
    name: str
    density: float  # kg/m^3 for bulk materials
    type: str  # "bulk", "surface" or "line"


@dataclass(frozen=True)
class Component:
    """One rocket component (nose cone, body tube, fin set, ...) in the XML tree."""

    element: ET.Element
    depth: int  # 0 = rocket, 1 = stage, 2 = parts of a stage, ...
    parent: Component | None = None  # e.g. the body tube a fin set is mounted on

    @property
    def kind(self) -> str:
        return self.element.tag

    @property
    def name(self) -> str:
        return (self.element.findtext("name") or "").strip()

    def has(self, tag: str) -> bool:
        return self.element.find(tag) is not None

    def text(self, tag: str) -> str:
        child = self.element.find(tag)
        if child is None or child.text is None:
            raise KeyError(f"{self.kind} '{self.name}' has no <{tag}>")
        return child.text.strip()

    def get(self, tag: str) -> FieldValue:
        return parse_value(self.text(tag))

    def number(self, tag: str) -> float:
        return self.get(tag).value

    def set(self, tag: str, value: float) -> None:
        """Write a numeric field. An explicit value turns OpenRocket's 'auto' off.

        Only existing fields can be set: inventing XML that OpenRocket doesn't expect
        could produce a file it rejects.
        """
        child = self.element.find(tag)
        if child is None:
            raise KeyError(f"{self.kind} '{self.name}' has no <{tag}> to set")
        child.text = repr(float(value))

    def material(self, tag: str = "material") -> Material | None:
        """The component's material, or another material field such as 'linematerial'."""
        child = self.element.find(tag)
        if child is None:
            return None
        return Material(
            name=(child.text or "").strip(),
            density=float(child.get("density", "nan")),
            type=child.get("type", ""),
        )


@dataclass(frozen=True)
class MotorRef:
    """A motor selected in a motor mount. The digest pins the exact thrust curve.

    One designation can have several curves in OpenRocket's database, and they can disagree:
    v1's 229H255-14A curve (digest 6e3ee79b...) had 316 Ns. See docs/case-study-229H255.md.
    """

    mount: str  # name of the component holding the motor mount
    designation: str
    manufacturer: str
    digest: str
    delay: float | None  # ejection delay in s; None for a plugged motor


def motors_in(component: Component) -> list[MotorRef]:
    motors = []
    for motor in component.element.iterfind("motormount/motor"):
        delay_text = (motor.findtext("delay") or "none").strip()
        motors.append(
            MotorRef(
                mount=component.name,
                designation=(motor.findtext("designation") or "").strip(),
                manufacturer=(motor.findtext("manufacturer") or "").strip(),
                digest=(motor.findtext("digest") or "").strip(),
                delay=None if delay_text == "none" else float(delay_text),
            )
        )
    return motors


def iter_components(
    element: ET.Element, depth: int = 0, parent: Component | None = None
) -> Iterator[Component]:
    """Walk the component tree depth-first. Children live under <subcomponents>."""
    component = Component(element, depth, parent)
    yield component
    for child in element.findall("subcomponents/*"):
        yield from iter_components(child, depth + 1, component)


def key_dimensions(component: Component) -> dict[str, float | str]:
    """The few numbers that describe a component at a glance. SI units."""
    c = component
    if c.kind == "nosecone":
        return {
            "shape": c.text("shape"),
            "length": c.number("length"),
            "diameter": 2 * c.number("aftradius"),
            "thickness": c.number("thickness"),
        }
    if c.kind == "bodytube":
        radius = c.number("radius")
        thickness = c.number("thickness")
        return {
            "length": c.number("length"),
            "outer diameter": 2 * radius,
            "inner diameter": 2 * (radius - thickness),
        }
    if c.kind == "trapezoidfinset":
        return {
            "fins": c.number("fincount"),
            "root": c.number("rootchord"),
            "tip": c.number("tipchord"),
            "height": c.number("height"),
            "sweep": c.number("sweeplength"),
            "thickness": c.number("thickness"),
        }
    if c.kind == "masscomponent":
        return {"mass": c.number("mass")}
    return {}
