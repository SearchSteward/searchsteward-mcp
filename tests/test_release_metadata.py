"""The three places a release version lives, and the registry's description limit (B, 2026-11-07).

A release bumps pyproject.toml, searchsteward_mcp/__init__.py and server.json (its top-level version AND packages[0].version) together. Nothing checked that
they agree, and two open PRs already carry titles that name a version their files do not. The official MCP Registry also caps `description` at 100 characters.
"""
import json
import pathlib
import re

import searchsteward_mcp

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def _pyproject_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert m, "pyproject.toml has no version line"
    return m.group(1)


def test_server_json_top_level_version_equals_pyproject():
    assert SERVER["version"] == _pyproject_version()


def test_server_json_package_version_equals_pyproject():
    assert len(SERVER["packages"]) == 1
    assert SERVER["packages"][0]["version"] == _pyproject_version()


def test_server_json_two_version_fields_agree():
    assert SERVER["version"] == SERVER["packages"][0]["version"]


def test_package_dunder_version_equals_pyproject():
    assert searchsteward_mcp.__version__ == _pyproject_version()


def test_server_json_description_fits_the_registry_limit():
    assert 0 < len(SERVER["description"]) <= 100, len(SERVER["description"])
