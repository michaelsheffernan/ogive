"""Fin flutter screening: Martin's relation from NACA TN 4197 (1958), corrected constant.

    Vf = a * sqrt( G / ( K * AR^3 * (lambda + 1) * P / (2 * (AR + 2) * (t/c)^3) ) )
    K = 39.3 / 14.696

a: speed of sound, G: fin shear modulus, P: static pressure (G and P in the same units),
AR = span^2 / planform area, lambda = tip / root chord, t/c = thickness / root chord.

Apogee Peak of Flight #291 printed K = 1.337, which applies Martin's factor of 1/2 twice and
overestimates flutter speed by sqrt(2). Peak of Flight #615 (J. K. Bennett, 2023) and RocketPy
issue #1200 correct it. Code that still uses 1.337 is sqrt(2) optimistic.

Assumes a thin, solid, isotropic, cantilevered plate. For carbon layups it's a screening check,
not a clearance: G depends on the layup and must come from a datasheet or a test.

Ported from TheCheng2005/OpenRocket_MCP, src/main/java/io/github/openrocketmcp/calc/Flutter.java
(GPL-3.0-or-later, (C) 2026 OpenRocket MCP contributors). Ogive is GPL-3.0-or-later too.
"""

import math
from dataclasses import dataclass

NACA_CONSTANT = 39.3 / 14.696  # 2.674; Peak of Flight #291's 1.337 is half of this
POF_291_CONSTANT = 1.337  # kept only to test that the old form is sqrt(2) high


@dataclass(frozen=True)
class Fin:
    """One trapezoidal fin. SI units."""

    span: float  # m, root to tip (OpenRocket "height")
    root_chord: float  # m
    tip_chord: float  # m
    thickness: float  # m

    @property
    def area(self) -> float:
        return (self.root_chord + self.tip_chord) / 2 * self.span

    @property
    def aspect_ratio(self) -> float:
        return self.span**2 / self.area

    @property
    def taper(self) -> float:
        return self.tip_chord / self.root_chord

    @property
    def thickness_ratio(self) -> float:
        return self.thickness / self.root_chord


def flutter_velocity(
    fin: Fin,
    shear_modulus: float,
    speed_of_sound: float,
    pressure: float,
    constant: float = NACA_CONSTANT,
) -> float:
    """Flutter velocity in m/s. shear_modulus and pressure in Pa."""
    if min(shear_modulus, speed_of_sound, pressure, fin.span, fin.thickness) <= 0:
        raise ValueError("shear modulus, speed of sound, pressure, span and thickness must be > 0")
    ar, tc = fin.aspect_ratio, fin.thickness_ratio
    denominator = constant * ar**3 * (fin.taper + 1) * pressure / (2 * (ar + 2) * tc**3)
    return speed_of_sound * math.sqrt(shear_modulus / denominator)


def required_thickness(
    fin: Fin, shear_modulus: float, target_velocity: float, speed_of_sound: float, pressure: float
) -> float:
    """Fin thickness (m) that puts flutter exactly at target_velocity. Vf scales with t^1.5."""
    v = flutter_velocity(fin, shear_modulus, speed_of_sound, pressure)
    return fin.thickness * (target_velocity / v) ** (2 / 3)
