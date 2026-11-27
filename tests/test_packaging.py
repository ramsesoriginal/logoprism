from __future__ import annotations

import tomllib

import logoprism

from .conftest import REPO


def test_version_matches_pyproject():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert logoprism.__version__ == project["version"]


def test_changelog_mentions_current_version():
    assert f"[{logoprism.__version__}]" in (REPO / "CHANGELOG.md").read_text(encoding="utf-8")


def test_readme_assets_exist_and_use_absolute_urls():
    import re

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    prefix = "https://raw.githubusercontent.com/ramsesoriginal/logoprism/main/"
    urls = re.findall(r'(?:src|srcset)="([^"]+)"', readme)
    assert urls, "README should contain images"
    for url in urls:
        assert url.startswith(prefix), f"README image is not an absolute raw GitHub URL: {url}"
        assert (REPO / url.removeprefix(prefix)).is_file(), f"missing asset for {url}"
    assert "](docs/" not in readme and "](./" not in readme, "relative links break on PyPI"
