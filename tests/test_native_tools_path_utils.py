"""Tests for Pi's tool path resolution (``path-utils.ts``, READ2)."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from pipy_harness.native.tools.path_utils import (
    normalize_path,
    resolve_read_path,
    resolve_to_cwd,
)

CWD = Path("/work/project")


@pytest.fixture(autouse=True)
def _home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/user")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("src/a.py", "/work/project/src/a.py"),
        ("./src/../README.md", "/work/project/README.md"),
        ("../sibling/x.txt", "/work/sibling/x.txt"),
        ("/etc/hosts", "/etc/hosts"),
        ("/tmp/../var/log/", "/var/log"),
        ("//double/slash", "/double/slash"),
        ("~", "/home/user"),
        ("~/notes.txt", "/home/user/notes.txt"),
        ("~//notes.txt", "/home/user/notes.txt"),
        ("~/", "/home/user"),
        ("~other/x", "/work/project/~other/x"),
        ("a~b", "/work/project/a~b"),
        ("@src/a.py", "/work/project/src/a.py"),
        ("@~/notes.txt", "/home/user/notes.txt"),
        ("file:///tmp/My%20File.txt", "/tmp/My File.txt"),
        ("my file.txt", "/work/project/my file.txt"),
        ("a b　c d.txt", "/work/project/a b c d.txt"),
        (".", "/work/project"),
        ("", "/work/project"),
    ],
)
def test_resolve_to_cwd_matches_pi(value: str, expected: str) -> None:
    assert resolve_to_cwd(value, CWD) == Path(expected)


def test_normalize_path_strips_only_one_at_prefix() -> None:
    assert normalize_path("@@x") == "@x"


@pytest.mark.parametrize(
    "value",
    [
        "file://[broken/x.png",
        "file://example.com/etc/hosts",
        "file://user@localhost/tmp/x",
        "file://localhost:123/tmp/x",
    ],
)
def test_file_urls_node_rejects_raise_value_error(value: str) -> None:
    with pytest.raises(ValueError):
        resolve_to_cwd(value, CWD)


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "LocalHost"])
def test_localhost_file_url_is_a_path(host: str) -> None:
    assert resolve_to_cwd(f"file://{host}/tmp/x", CWD) == Path("/tmp/x")


def _exists_only(*names: str):
    existing = set(names)
    return lambda candidate: candidate in existing


def test_resolve_read_path_prefers_the_plain_path() -> None:
    plain = "/work/project/Screenshot 10.15 AM.png"
    variant = "/work/project/Screenshot 10.15 AM.png"

    resolved = resolve_read_path(
        "Screenshot 10.15 AM.png", CWD, exists=_exists_only(plain, variant)
    )

    assert resolved == Path(plain)


@pytest.mark.parametrize(
    ("typed", "on_disk"),
    [
        # macOS screenshot: narrow no-break space before AM/PM, any case.
        ("Shot 9.41.07 PM.png", "Shot 9.41.07 PM.png"),
        ("Shot 9.41.07 am.png", "Shot 9.41.07 am.png"),
        # NFD file names.
        ("café.txt", unicodedata.normalize("NFD", "café.txt")),
        # Curly apostrophe.
        ("Capture d'ecran.png", "Capture d’ecran.png"),
        # NFD plus curly apostrophe (French macOS screenshots).
        (
            "Capture d'écran.png",
            unicodedata.normalize("NFD", "Capture d’écran.png"),
        ),
    ],
)
def test_resolve_read_path_tries_macos_variants(typed: str, on_disk: str) -> None:
    target = f"/work/project/{on_disk}"

    assert resolve_read_path(typed, CWD, exists=_exists_only(target)) == Path(target)


def test_resolve_read_path_returns_the_plain_path_when_nothing_exists() -> None:
    assert resolve_read_path("missing 1 AM.txt", CWD, exists=_exists_only()) == Path(
        "/work/project/missing 1 AM.txt"
    )


def test_resolve_read_path_order_is_am_pm_then_nfd_then_curly() -> None:
    typed = "café d'x 1 AM.txt"
    am_pm = "/work/project/café d'x 1 AM.txt"
    nfd = unicodedata.normalize("NFD", "/work/project/café d'x 1 AM.txt")
    curly = "/work/project/café d’x 1 AM.txt"

    assert resolve_read_path(typed, CWD, exists=_exists_only(nfd, curly, am_pm)) == (
        Path(am_pm)
    )
    assert resolve_read_path(typed, CWD, exists=_exists_only(nfd, curly)) == Path(nfd)
    assert resolve_read_path(typed, CWD, exists=_exists_only(curly)) == Path(curly)
