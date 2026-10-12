"""Tests for ogive.physics.geometry (the team lead's formulas).

Expected values are worked by hand from shapes with known answers, not from the code under test.
"""

import math

import pytest

from ogive.physics.geometry import (
    cone_shell_volume,
    fin_fillet_area,
    trapezoid_fin_volume,
    tube_volume,
)


def test_tube_wall_volume():
    # Outer radius 2, wall 1 -> inner radius 1. Ring area = pi(4 - 1) = 3 pi. Length 1.
    assert tube_volume(outer_radius=2.0, thickness=1.0, length=1.0) == pytest.approx(3 * math.pi)


def test_tube_with_wall_equal_to_radius_is_a_solid_rod():
    # Solid cylinder r = 1, length 2: pi r^2 L = 2 pi.
    assert tube_volume(outer_radius=1.0, thickness=1.0, length=2.0) == pytest.approx(2 * math.pi)


def test_tube_rejects_wall_thicker_than_radius():
    with pytest.raises(ValueError):
        tube_volume(outer_radius=1.0, thickness=1.5, length=1.0)


def test_cone_shell_3_4_5():
    # Radius 3, length 4: a 3-4-5 triangle, so sin a = 3/5 and cos a = 4/5.
    # Inner cone: radius 3 - 0.1 / 0.8 = 2.875, length 4 - 0.1 / 0.6 = 3.8333...
    outer = math.pi / 3 * 3**2 * 4
    inner = math.pi / 3 * 2.875**2 * (4 - 0.1 / 0.6)
    assert cone_shell_volume(base_radius=3.0, length=4.0, thickness=0.1) == pytest.approx(
        outer - inner
    )


def test_cone_shell_thin_wall_limit_is_side_area_times_thickness():
    # As t -> 0 the shell volume -> side area x t = pi * 3 * 5 * t = 15 pi t.
    t = 1e-7
    assert cone_shell_volume(3.0, 4.0, t) / t == pytest.approx(15 * math.pi, rel=1e-5)


def test_cone_with_wall_too_thick_for_a_cavity_is_solid():
    assert cone_shell_volume(3.0, 4.0, thickness=10.0) == pytest.approx(math.pi / 3 * 9 * 4)


def test_fillet_area_with_fillet_radius_equal_to_tube_radius():
    # r = R = 1: the angles are 30 and 60 degrees. Per side sqrt(3)/2 - pi/6 - pi/12;
    # both sides together: sqrt(3) - pi/2.
    assert fin_fillet_area(1.0, 1.0) == pytest.approx(math.sqrt(3) - math.pi / 2)


def test_no_fillet_has_no_area():
    assert fin_fillet_area(0.0, 0.015) == 0.0


def test_rectangular_fin():
    # Root = tip = 2, height 3: a 2 x 3 rectangle, 0.5 thick.
    assert trapezoid_fin_volume(2.0, 2.0, 3.0, 0.5) == pytest.approx(3.0)


def test_triangular_fin():
    # Tip 0: a triangle with base 4, height 3 -> area 6, 1 thick.
    assert trapezoid_fin_volume(4.0, 0.0, 3.0, 1.0) == pytest.approx(6.0)
