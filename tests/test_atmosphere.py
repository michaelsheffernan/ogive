"""Tests for ogive.physics.atmosphere.

Expected values come from standard tables, not from our own formula,
so a wrong formula can't pass by agreeing with itself.
"""

import pytest

from ogive.physics.atmosphere import speed_of_sound


@pytest.mark.parametrize(
    ("temperature_c", "expected_m_s"),
    [
        (0.0, 331.3),  # classic textbook value
        (10.0, 337.3),  # our reference launch temperature
        (15.0, 340.3),  # ISA sea level, 340.294 m/s
    ],
)
def test_matches_standard_tables(temperature_c, expected_m_s):
    assert speed_of_sound(temperature_c) == pytest.approx(expected_m_s, abs=0.1)


def test_rises_with_temperature():
    assert speed_of_sound(30.0) > speed_of_sound(-10.0)


def test_is_zero_at_absolute_zero():
    assert speed_of_sound(-273.15) == pytest.approx(0.0)


def test_rejects_below_absolute_zero():
    with pytest.raises(ValueError):
        speed_of_sound(-300.0)
