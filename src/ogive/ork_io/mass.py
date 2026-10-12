"""Component masses from .ork geometry and materials. Backend label: ogive geometry v0.

Not OpenRocket's numbers, but built to match OpenRocket 24.12's mass model, and
cross-checked against it in tests/test_openrocket_crosscheck.py:
- recovery (RecoveryDevice/Parachute/Streamer.java): fabric area x surface density,
  plus line count x line length x line density
- fins (FinSet.calculateCM): plate x cross-section factor, plus fillets along the root
Not modelled, so returned as None, never guessed: nose shapes other than conical,
nose shoulders, fin tabs, fillets on anything but a body tube.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ogive.ork_io.components import Component
from ogive.physics import geometry

BACKEND = "ogive geometry v0"

# OpenRocket 24.12 FinSet.CrossSection relative volumes.
FIN_CROSS_SECTION_FACTOR = {"square": 1.00, "rounded": 0.99, "airfoil": 0.85}


def component_mass(c: Component) -> float | None:
    """Mass in kg of this component alone (children excluded), or None if not modelled."""
    if c.has("overridemass"):
        return c.number("overridemass")  # OpenRocket uses the override, so do we
    if c.kind == "masscomponent":
        return c.number("mass")
    material = c.material()
    if material is None:
        return None
    if material.type == "surface":
        return _recovery_mass(c, material.density)
    if material.type != "bulk":
        return None
    if c.kind == "nosecone":
        has_shoulder = c.has("aftshoulderlength") and c.number("aftshoulderlength") > 0
        if c.text("shape") != "conical" or has_shoulder:
            return None
        volume = geometry.cone_shell_volume(
            c.number("aftradius"), c.number("length"), c.number("thickness")
        )
        return volume * material.density
    if c.kind == "bodytube":
        volume = geometry.tube_volume(c.number("radius"), c.number("thickness"), c.number("length"))
        return volume * material.density
    if c.kind == "trapezoidfinset":
        return _fin_set_mass(c, material.density)
    return None


def _fin_set_mass(c: Component, density: float) -> float | None:
    """Every fin: flat plate x cross-section factor, plus the root fillets."""
    factor = FIN_CROSS_SECTION_FACTOR.get(c.text("crosssection"))
    has_tab = c.has("tabheight") and c.number("tabheight") > 0
    if factor is None or has_tab:
        return None
    plate = geometry.trapezoid_fin_volume(
        c.number("rootchord"), c.number("tipchord"), c.number("height"), c.number("thickness")
    )
    one_fin = plate * factor * density

    fillet_radius = c.number("filletradius") if c.has("filletradius") else 0.0
    if fillet_radius > 0:
        fillet_material = c.material("filletmaterial")
        if fillet_material is None or c.parent is None or c.parent.kind != "bodytube":
            return None  # fillet mass unknown: don't guess
        area = geometry.fin_fillet_area(fillet_radius, c.parent.number("radius"))
        one_fin += area * c.number("rootchord") * fillet_material.density
    return one_fin * c.number("fincount")


def _recovery_mass(c: Component, surface_density: float) -> float | None:
    """Parachute or streamer: fabric area x kg/m^2, plus shroud lines x kg/m."""
    if c.kind == "parachute":
        canopy_area = math.pi * (c.number("diameter") / 2) ** 2
        line_material = c.material("linematerial")
        if line_material is None:
            return None  # unknown line mass: don't guess
        lines = c.number("linecount") * c.number("linelength") * line_material.density
        return canopy_area * surface_density + lines
    if c.kind == "streamer":
        return c.number("striplength") * c.number("stripwidth") * surface_density
    return None


@dataclass(frozen=True)
class MassSummary:
    total: float  # kg, sum over modelled components
    unmodelled: list[str]  # names of components that contribute mass we couldn't compute


# Components that are pure containers: no mass of their own.
CONTAINER_KINDS = {"rocket", "stage"}


def dry_mass(components: list[Component]) -> MassSummary:
    """Rocket mass without motor."""
    total = 0.0
    unmodelled = []
    for c in components:
        if c.kind in CONTAINER_KINDS:
            continue
        mass = component_mass(c)
        if mass is None:
            unmodelled.append(c.name)
        else:
            total += mass
    return MassSummary(total, unmodelled)
