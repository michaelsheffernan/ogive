"""Shared test fixtures.

Two kinds of data file:
- the live reference (data/blackhawk29_v2.ork). The team lead re-saves it from OpenRocket, so
  tests on it check only the locked design, not stored sims or conditions.
- frozen regression files (data/regression/). Never edited. Tests may pin anything in them.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
REFERENCE_ORK = DATA / "blackhawk29_v2.ork"
V1_ORK = DATA / "regression" / "heloria_blackhawk29_v1.ork"  # wrong 229H255 curve (316 Ns)
V2_LOCKED_ORK = DATA / "regression" / "blackhawk29_v2_locked.ork"  # v2 as locked, 4 mixed sims


@pytest.fixture
def reference_ork() -> Path:
    return REFERENCE_ORK


@pytest.fixture
def v1_ork() -> Path:
    return V1_ORK


@pytest.fixture
def v2_locked_ork() -> Path:
    return V2_LOCKED_ORK


@pytest.fixture(scope="session")
def running_openrocket():
    """OpenRocket started once per test session; tests using it skip where it isn't installed."""
    from ogive.sim import openrocket

    try:
        return openrocket.start()
    except openrocket.OpenRocketUnavailable as error:
        pytest.skip(str(error))
