"""Volumes of rocket parts. Mass = volume x material density (done in ork_io/mass.py).

All inputs and outputs are SI: metres in, cubic metres out.
"""

import math


def tube_volume(outer_radius: float, thickness: float, length: float) -> float:
    """Volume of the wall of a hollow cylinder (a body tube). Exact.

    The wall's cross-section is a ring: pi * (R^2 - r^2), with inner radius r = R - thickness.
    Raises ValueError if thickness > outer_radius (the wall can't be thicker than the tube).
    """
    if thickness > outer_radius:
        raise ValueError(f"wall {thickness} m is thicker than the tube radius {outer_radius} m")
    inner_radius = outer_radius - thickness
    ring_area = math.pi * (outer_radius**2 - inner_radius**2)
    return ring_area * length


def cone_shell_volume(base_radius: float, length: float, thickness: float) -> float:
    """Volume of a hollow cone with an open base (a conical nose cone). Exact.

    Outer cone minus inner cone. The inner surface is parallel to the outer one, `thickness`
    inside it. With half-angle a (tan a = r / L), the inner cone keeps angle a, its base radius
    shrinks by t / cos a and its apex moves back by t / sin a.
    A wall thicker than that leaves no cavity: a solid cone.
    Matches OpenRocket 24.12 on the reference nose (14.65 g). The thin-shell shortcut
    (side area x t) over-counts by about t / r, which is 7 % there.
    """
    half_angle = math.atan2(base_radius, length)
    outer = math.pi / 3 * base_radius**2 * length
    inner_radius = base_radius - thickness / math.cos(half_angle)
    inner_length = length - thickness / math.sin(half_angle)
    if inner_radius <= 0 or inner_length <= 0:
        return outer
    inner = math.pi / 3 * inner_radius**2 * inner_length
    return outer - inner


def trapezoid_fin_volume(
    root_chord: float, tip_chord: float, height: float, thickness: float
) -> float:
    """Volume of ONE flat trapezoidal fin. Exact for a flat plate.

    Planform area = (root + tip) / 2 x height. Sweep doesn't appear: sliding the tip chord
    along doesn't change the area.
    """
    planform_area = (root_chord + tip_chord) / 2 * height
    return planform_area * thickness


def fin_fillet_area(fillet_radius: float, body_radius: float) -> float:
    """Cross-section area of the fillets at one fin root, both sides together.

    Source: OpenRocket 24.12 FinSet.calculateFilletCrossSection. A concave circular fillet
    is tangent to the flat fin and the round tube. Per side: the triangle (tube centre,
    fillet centre, fin tangent point) minus the fillet-circle and tube-circle sectors in it.
    Multiply by the root chord for the volume.
    """
    if fillet_radius <= 0:
        return 0.0
    hypotenuse = fillet_radius + body_radius
    tube_angle = math.asin(fillet_radius / hypotenuse)
    fillet_angle = math.acos(fillet_radius / hypotenuse)
    triangle = math.tan(fillet_angle) * fillet_radius**2 / 2
    one_side = triangle - fillet_angle * fillet_radius**2 / 2 - tube_angle * body_radius**2 / 2
    return 2 * one_side
