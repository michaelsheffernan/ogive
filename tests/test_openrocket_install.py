"""Finding OpenRocket and a Java to run it, on Windows, macOS and Linux layouts.

Fake installs in tmp_path: empty files where the jar and the JVM library would be, plus a Java
`release` file. Nothing here starts a JVM, so these run on every CI runner. The Linux CI job
(`openrocket-linux` in .github/workflows/ci.yml) is the real proof: it runs the OpenRocket tests
against the released OpenRocket-24.12.jar and a system Java 17.
"""

import sys
from pathlib import Path

import pytest

from ogive.sim import openrocket
from ogive.sim.openrocket import OpenRocketUnavailable, find_install


def touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def java_home(root: Path, library: str, version: str = "17.0.12") -> Path:
    """A fake Java home: the JVM library at its OS path and a release file."""
    touch(root / "release", f'IMPLEMENTOR="Eclipse Adoptium"\nJAVA_VERSION="{version}"\n')
    return touch(root / library)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("OPENROCKET_HOME", raising=False)
    monkeypatch.delenv("JAVA_HOME", raising=False)


def test_windows_install_uses_its_bundled_java(tmp_path):
    jar = touch(tmp_path / "jar" / "OpenRocket-24.12.jar")
    jvm = java_home(tmp_path / "jre", "bin/server/jvm.dll")
    install = find_install(tmp_path)
    assert (install.jar, install.jvm, install.version) == (jar, jvm, "24.12")
    assert install.backend == "OpenRocket 24.12"


def test_linux_install_uses_its_bundled_java(tmp_path):
    jar = touch(tmp_path / "jar" / "OpenRocket-24.12.jar")
    jvm = java_home(tmp_path / "jre", "lib/server/libjvm.so")
    install = find_install(tmp_path)
    assert (install.jar, install.jvm) == (jar, jvm)


def test_nested_app_bundle_is_searched(tmp_path):
    """A macOS-style .app with the jar and Java several folders down (exact layout unverified)."""
    app = tmp_path / "OpenRocket.app"
    jar = touch(app / "Contents" / "Resources" / "app" / "jar" / "OpenRocket-24.12.jar")
    home = app / "Contents" / "Resources" / "jre.bundle" / "Contents" / "Home"
    jvm = java_home(home, "lib/server/libjvm.dylib")
    install = find_install(app)
    assert (install.jar, install.jvm, install.home) == (jar, jvm, app)


def test_a_jar_on_its_own_runs_on_java_home(tmp_path, monkeypatch):
    jar = touch(tmp_path / "Downloads" / "OpenRocket-24.12.jar")
    jvm = java_home(tmp_path / "jdk-17", "lib/server/libjvm.so")
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "jdk-17"))
    monkeypatch.setenv("OPENROCKET_HOME", str(jar))
    install = find_install()
    assert (install.jar, install.jvm, install.version) == (jar, jvm, "24.12")


def test_a_loose_jar_never_borrows_a_neighbouring_jdk(tmp_path, monkeypatch):
    """A JDK that happens to sit next to the jar isn't 'bundled': JAVA_HOME wins."""
    jar = touch(tmp_path / "OpenRocket-24.12.jar")
    java_home(tmp_path / "someones-jdk", "lib/server/libjvm.so", version="11.0.2")
    chosen = java_home(tmp_path / "elsewhere" / "jdk-21", "lib/server/libjvm.so", version="21.0.4")
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "elsewhere" / "jdk-21"))
    assert find_install(jar).jvm == chosen


@pytest.mark.parametrize("version", ["11.0.2", "1.8.0_292"])
def test_java_older_than_17_is_refused_with_the_reason(tmp_path, monkeypatch, version):
    jar = touch(tmp_path / "OpenRocket-24.12.jar")
    java_home(tmp_path / "old-java", "lib/server/libjvm.so", version=version)
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "old-java"))
    with pytest.raises(OpenRocketUnavailable, match=f"needs Java 17 or newer.*{version}"):
        find_install(jar)


def test_java_home_without_a_runtime_is_named(tmp_path, monkeypatch):
    jar = touch(tmp_path / "OpenRocket-24.12.jar")
    (tmp_path / "empty-java").mkdir()
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "empty-java"))
    with pytest.raises(OpenRocketUnavailable, match="JAVA_HOME is .*empty-java"):
        find_install(jar)


def test_missing_install_says_where_it_looked_and_what_to_set(tmp_path):
    with pytest.raises(OpenRocketUnavailable, match="nowhere.*OPENROCKET_HOME"):
        find_install(tmp_path / "nowhere")


@pytest.mark.parametrize("unset", ["", "   ", "${user_config.openrocket_home}"])
def test_unset_or_placeholder_home_searches_the_default_places(tmp_path, monkeypatch, unset):
    """Claude Desktop passes an empty string, or in some builds the raw placeholder."""
    jar = touch(tmp_path / "jar" / "OpenRocket-24.12.jar")
    java_home(tmp_path / "jre", "lib/server/libjvm.so")
    monkeypatch.setitem(openrocket.DEFAULT_HOMES, sys.platform, [tmp_path / "absent", tmp_path])
    monkeypatch.setenv("OPENROCKET_HOME", unset)
    assert find_install().jar == jar


def test_every_os_has_default_places():
    assert {"win32", "darwin", "linux"} <= set(openrocket.DEFAULT_HOMES)


def test_search_is_bounded(tmp_path):
    """A wrong home (a whole drive) fails fast instead of crawling it."""
    deep = tmp_path.joinpath(*[f"d{i}" for i in range(12)])
    touch(deep / "OpenRocket-24.12.jar")
    with pytest.raises(OpenRocketUnavailable):
        find_install(tmp_path)


def test_newest_jar_wins(tmp_path):
    touch(tmp_path / "jar" / "OpenRocket-23.09.jar")
    newest = touch(tmp_path / "jar" / "OpenRocket-24.12.jar")
    java_home(tmp_path / "jre", "lib/server/libjvm.so")
    assert find_install(tmp_path).jar == newest
