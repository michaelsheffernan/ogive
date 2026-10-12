"""Flutter formula.

Cases ported from TheCheng2005/OpenRocket_MCP FlutterTest.java (GPL-3.0-or-later).

The first test rebuilds Martin's equation exactly as NACA TN 4197 prints it (psi units, P/P0),
an independent form, so a wrong constant can't pass by agreeing with itself.
"""

import math

import pytest

from ogive.physics.flutter import (
    NACA_CONSTANT,
    POF_291_CONSTANT,
    Fin,
    flutter_velocity,
    required_thickness,
)

# Trapezoid: span 100 mm, root 150 mm, tip 50 mm, 3 mm thick. Area 0.01 m^2, AR 1.
FIN = Fin(span=0.1, root_chord=0.15, tip_chord=0.05, thickness=0.003)
G10, A, P = 2.9e9, 340.29, 101325.0  # G10 fibreglass, sea level


def test_matches_naca_tn4197_as_printed_in_psi():
    g_psi = G10 / 6894.757293168
    ar, lam, tc = 1.0, 1 / 3, 0.02
    denominator = 39.3 * ar**3 / (tc**3 * (ar + 2)) * (lam + 1) / 2 * (P / 101325)
    expected = A * math.sqrt(g_psi / denominator)
    assert flutter_velocity(FIN, G10, A, P) == pytest.approx(expected, rel=1e-5)
    assert NACA_CONSTANT == pytest.approx(2.674, abs=1e-3)


def test_peak_of_flight_291_constant_is_sqrt2_optimistic():
    corrected = flutter_velocity(FIN, G10, A, P)
    old = flutter_velocity(FIN, G10, A, P, constant=POF_291_CONSTANT)
    assert old / corrected == pytest.approx(math.sqrt(2), rel=1e-3)


def test_scaling_laws():
    v = flutter_velocity(FIN, G10, A, P)
    thick = Fin(0.1, 0.15, 0.05, 0.006)
    assert flutter_velocity(thick, G10, A, P) / v == pytest.approx(2**1.5)  # Vf ~ t^1.5
    assert flutter_velocity(FIN, 4 * G10, A, P) / v == pytest.approx(2)  # Vf ~ sqrt(G)
    assert flutter_velocity(FIN, G10, A, P / 2) / v == pytest.approx(math.sqrt(2))  # Vf ~ 1/sqrt(P)
    tall = Fin(span=0.2, root_chord=0.15, tip_chord=0.05, thickness=0.003)
    assert flutter_velocity(tall, G10, A, P) < v  # longer span flutters earlier


def test_required_thickness_round_trips():
    t = required_thickness(FIN, G10, 600.0, A, P)
    assert flutter_velocity(Fin(0.1, 0.15, 0.05, t), G10, A, P) == pytest.approx(600.0)


def test_rejects_nonsense_inputs():
    with pytest.raises(ValueError):
        flutter_velocity(FIN, 0.0, A, P)
