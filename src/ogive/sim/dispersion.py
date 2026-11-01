"""Landing dispersion: where the rocket comes down when the wind isn't exactly what the sim says.

A Monte Carlo over real OpenRocket flights. Each run flies the same design and simulation with:
- its own turbulence seed (seed, seed + 1, ...),
- a wind speed drawn from Normal(stored average, speed_sd), clipped at 0,
- a wind direction drawn from Normal(stored direction, direction_sd).
Everything else is as stored in the file (rod, site, atmosphere, "launch into wind"). The draws
come from random.Random(seed), so the same call always flies the same runs.

Wind direction is the compass bearing the wind blows FROM, clockwise from north. That's
OpenRocket's convention, measured on 2026-11-01 (not assumed): 10 m/s at 0 deg drifts the
reference rocket 670 m south, at 90 deg 671 m west. tests/test_server_openrocket.py pins it.

The 95 % ellipse comes from the sample covariance of the landing points: semi-axes
sqrt(5.991 * eigenvalue), 5.991 being the chi-square value for 2 degrees of freedom at 95 %.
It assumes the landings scatter roughly like a 2D normal. With ~20 runs it's a rough estimate,
so the farthest run is always reported next to it.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

CHI2_2DOF_95 = 5.991


@dataclass(frozen=True)
class Landing:
    seed: int
    wind_speed: float  # m/s, this run's average wind
    wind_from: float  # deg, compass bearing the wind blows from
    east: float  # m from the pad
    north: float  # m from the pad
    apogee: float  # m
    ground_hit_velocity: float  # m/s, total speed (OpenRocket 24.12)

    @property
    def distance(self) -> float:
        return math.hypot(self.east, self.north)

    @property
    def bearing(self) -> float:
        """Compass bearing from the pad to the landing, deg."""
        return bearing(self.east, self.north)


@dataclass(frozen=True)
class Ellipse:
    east: float  # centre, m from the pad
    north: float
    semi_major: float  # m
    semi_minor: float  # m
    major_bearing: float  # deg, compass bearing of the long axis, in [0, 180)


@dataclass(frozen=True)
class Dispersion:
    backend: str
    file: str
    simulation: str
    seed: int
    wind_average: float  # m/s, as stored in the sim
    wind_from: float  # deg, as stored in the sim
    speed_sd: float  # m/s
    direction_sd: float  # deg
    landings: list[Landing]
    ellipse: Ellipse  # 95 %
    field_radius: float | None  # m; when given, how many runs land outside it

    @property
    def farthest(self) -> Landing:
        return max(self.landings, key=lambda landing: landing.distance)

    @property
    def outside(self) -> int | None:
        if self.field_radius is None:
            return None
        return sum(landing.distance > self.field_radius for landing in self.landings)


def bearing(east: float, north: float) -> float:
    return math.degrees(math.atan2(east, north)) % 360.0


def ellipse_95(points: list[tuple[float, float]]) -> Ellipse:
    """The 95 % ellipse of 2D points (east, north): sample covariance, closed-form eigenvalues."""
    n = len(points)
    if n < 3:
        raise ValueError(f"need at least 3 points for an ellipse, got {n}")
    me = sum(p[0] for p in points) / n
    mn = sum(p[1] for p in points) / n
    see = sum((p[0] - me) ** 2 for p in points) / (n - 1)
    snn = sum((p[1] - mn) ** 2 for p in points) / (n - 1)
    sen = sum((p[0] - me) * (p[1] - mn) for p in points) / (n - 1)
    mid = (see + snn) / 2
    half = math.hypot((see - snn) / 2, sen)
    big, small = mid + half, max(mid - half, 0.0)
    # Long axis angle from the east axis (counterclockwise), then as a compass bearing.
    angle = 0.5 * math.atan2(2 * sen, see - snn)
    major_bearing = (90.0 - math.degrees(angle)) % 180.0
    return Ellipse(
        east=me,
        north=mn,
        semi_major=math.sqrt(CHI2_2DOF_95 * big),
        semi_minor=math.sqrt(CHI2_2DOF_95 * small),
        major_bearing=major_bearing,
    )


def draw_winds(
    runs: int, seed: int, average: float, wind_from: float, speed_sd: float, direction_sd: float
) -> list[tuple[float, float]]:
    """(speed m/s, from deg) per run. Seeded, so the same inputs give the same draws."""
    rng = random.Random(seed)
    return [
        (max(0.0, rng.gauss(average, speed_sd)), (wind_from + rng.gauss(0.0, direction_sd)) % 360)
        for _ in range(runs)
    ]


def landing_dispersion(
    path: str | Path,
    simulation: str | None = None,
    runs: int = 20,
    seed: int = 0,
    speed_sd: float = 2.0,
    direction_sd: float = 20.0,
    field_radius: float | None = None,
    progress=None,
) -> Dispersion:
    """Fly `runs` OpenRocket flights with scattered wind and summarise where they land.

    progress(done, total) is called after each flight, if given.
    """
    from ogive.sim import openrocket

    if not 3 <= runs <= 200:
        raise ValueError(f"runs must be 3 to 200, got {runs}")
    if speed_sd < 0 or direction_sd < 0:
        raise ValueError("speed_sd and direction_sd can't be negative")
    name, average, direction = openrocket.stored_wind(path, simulation)
    wind_from = math.degrees(direction) % 360
    winds = draw_winds(runs, seed, average, wind_from, speed_sd, direction_sd)
    landings, backend = [], ""
    for i, (speed, frm) in enumerate(winds):
        flight = openrocket.simulate(
            path, name, seed + i, wind_speed=speed, wind_direction=math.radians(frm)
        )
        backend = flight.backend
        landings.append(
            Landing(
                seed=seed + i,
                wind_speed=speed,
                wind_from=frm,
                east=flight.series["east"][-1],
                north=flight.series["north"][-1],
                apogee=flight.summary.apogee,
                ground_hit_velocity=flight.summary.ground_hit_velocity,
            )
        )
        if progress is not None:
            progress(i + 1, runs)
    return Dispersion(
        backend=backend,
        file=str(path),
        simulation=name,
        seed=seed,
        wind_average=average,
        wind_from=wind_from,
        speed_sd=speed_sd,
        direction_sd=direction_sd,
        landings=landings,
        ellipse=ellipse_95([(landing.east, landing.north) for landing in landings]),
        field_radius=field_radius,
    )
