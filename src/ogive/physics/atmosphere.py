"""Atmosphere: properties of air that the sims and the judge need."""

import math

GAMMA_AIR = 1.4  # ratio of specific heats (cp / cv) for dry air, dimensionless
R_AIR = 287.05  # specific gas constant for dry air, J/(kg*K)
ABSOLUTE_ZERO_C = -273.15  # 0 K expressed in degrees Celsius


def speed_of_sound(temperature_c: float) -> float:
    """Return the speed of sound in dry air, in m/s.

    temperature_c: air temperature in degrees Celsius.
    Raises ValueError if the temperature is below absolute zero.

    Ideal gas: a = sqrt(gamma * R * T), with T in kelvin.
    """
    temperature_k = temperature_c - ABSOLUTE_ZERO_C
    if temperature_k < 0:
        raise ValueError(f"{temperature_c} C is below absolute zero ({ABSOLUTE_ZERO_C} C)")
    return math.sqrt(GAMMA_AIR * R_AIR * temperature_k)
