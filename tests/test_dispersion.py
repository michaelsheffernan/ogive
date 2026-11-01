"""Landing dispersion maths and map, without OpenRocket. The real flights are in
tests/test_server_openrocket.py (wind convention, determinism, the MCP tool)."""

import io
import math
import random

import pytest

from ogive.sim.dispersion import (
    CHI2_2DOF_95,
    Dispersion,
    Landing,
    bearing,
    draw_winds,
    ellipse_95,
)
from ogive.viewer import landing_map


def scatter(sd_major: float, sd_minor: float, axis_bearing: float, n: int = 4000, seed: int = 1):
    """Normal points around (100, -50) with known spreads along a known compass axis."""
    rng = random.Random(seed)
    b = math.radians(axis_bearing)
    ux, uy, vx, vy = math.sin(b), math.cos(b), math.cos(b), -math.sin(b)
    points = []
    for _ in range(n):
        a, c = rng.gauss(0, sd_major), rng.gauss(0, sd_minor)
        points.append((100 + a * ux + c * vx, -50 + a * uy + c * vy))
    return points


@pytest.mark.parametrize("axis", [0.0, 30.0, 90.0, 135.0])
def test_ellipse_recovers_spread_and_axis(axis):
    e = ellipse_95(scatter(40.0, 10.0, axis))
    assert e.east == pytest.approx(100, abs=2) and e.north == pytest.approx(-50, abs=2)
    assert e.semi_major == pytest.approx(math.sqrt(CHI2_2DOF_95) * 40, rel=0.05)
    assert e.semi_minor == pytest.approx(math.sqrt(CHI2_2DOF_95) * 10, rel=0.05)
    assert min(abs(e.major_bearing - axis), 180 - abs(e.major_bearing - axis)) < 2


def test_ellipse_holds_95_percent_of_normal_points():
    points = scatter(40.0, 10.0, 60.0, n=20000, seed=7)
    e = ellipse_95(points)
    b = math.radians(e.major_bearing)
    ux, uy = math.sin(b), math.cos(b)
    inside = 0
    for x, y in points:
        dx, dy = x - e.east, y - e.north
        a, c = dx * ux + dy * uy, dx * uy - dy * ux
        inside += (a / e.semi_major) ** 2 + (c / e.semi_minor) ** 2 <= 1
    assert inside / len(points) == pytest.approx(0.95, abs=0.01)


def test_ellipse_needs_three_points():
    with pytest.raises(ValueError, match="at least 3"):
        ellipse_95([(0, 0), (1, 1)])


def test_identical_points_give_a_zero_ellipse():
    e = ellipse_95([(5.0, 5.0)] * 4)
    assert (e.semi_major, e.semi_minor) == (0.0, 0.0)


def test_bearing_is_compass():
    assert bearing(0, 1) == 0 and bearing(1, 0) == 90 and bearing(0, -1) == 180
    assert bearing(-1, 0) == 270


def test_wind_draws_are_seeded_and_never_negative():
    a = draw_winds(50, 3, average=1.0, wind_from=350.0, speed_sd=3.0, direction_sd=30.0)
    assert a == draw_winds(50, 3, average=1.0, wind_from=350.0, speed_sd=3.0, direction_sd=30.0)
    assert a != draw_winds(50, 4, average=1.0, wind_from=350.0, speed_sd=3.0, direction_sd=30.0)
    assert all(speed >= 0 and 0 <= frm < 360 for speed, frm in a)
    assert any(speed == 0 for speed, _ in a)  # clipped, not dropped


def test_zero_spread_flies_the_stored_wind():
    assert set(draw_winds(5, 0, 4.0, 90.0, 0.0, 0.0)) == {(4.0, 90.0)}


def fake(field_radius=None) -> Dispersion:
    landings = [
        Landing(i, 3.0, 90.0, -100.0 - 30 * i, 10.0 * (-1) ** i, 1900.0, 9.0) for i in range(8)
    ]
    return Dispersion(
        backend="OpenRocket 24.12",
        file="x.ork",
        simulation="Sim",
        seed=0,
        wind_average=3.0,
        wind_from=90.0,
        speed_sd=2.0,
        direction_sd=20.0,
        landings=landings,
        ellipse=ellipse_95([(lg.east, lg.north) for lg in landings]),
        field_radius=field_radius,
    )


def test_farthest_and_outside_count():
    d = fake(field_radius=260.0)
    assert d.farthest.seed == 7
    assert d.outside == sum(lg.distance > 260 for lg in d.landings) == 2
    assert fake().outside is None


def test_map_is_a_png_of_the_stated_size():
    from PIL import Image

    image = Image.open(io.BytesIO(landing_map.to_png(fake(field_radius=300.0))))
    assert image.format == "PNG"
    assert image.size == (landing_map.WIDTH, landing_map.HEIGHT)


@pytest.mark.parametrize(
    ("extent", "step"), [(8, 5), (40, 10), (130, 50), (450, 100), (1800, 500), (9e4, 10000)]
)
def test_ring_step_needs_at_most_five_rings(extent, step):
    assert landing_map.ring_step(extent) == step
