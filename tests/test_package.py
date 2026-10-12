"""Smoke tests: the package installs and every architecture layer imports."""

import importlib

import pytest

import ogive

LAYERS = [
    "ogive.physics",
    "ogive.ork_io",
    "ogive.sim",
    "ogive.judge",
    "ogive.opt",
    "ogive.server",
]


def test_version_is_set():
    assert ogive.__version__


@pytest.mark.parametrize("module", LAYERS)
def test_layer_imports(module):
    importlib.import_module(module)
