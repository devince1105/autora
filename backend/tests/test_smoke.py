import importlib

import autora


def test_package_importable():
    assert autora.__version__


def test_layer_packages_exist():
    for name in ("runtime", "company", "realtime", "domains", "db", "infra"):
        importlib.import_module(f"autora.{name}")
