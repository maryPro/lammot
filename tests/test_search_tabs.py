import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_search_tabs_js():
    node = shutil.which("node")
    if node is None:
        if os.getenv("CI"):
            pytest.fail("node is required in CI")
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests" / "js" / "search-tabs.test.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_index_loads_search_tabs_before_app():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'id="search-tab-songs"' in html
    assert 'id="search-tab-videos"' in html
    assert html.index("/search-tabs.js") < html.index("/app.js")


def test_app_sends_the_selected_search_type():
    source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "SearchTabs.apiType(state.searchType)" in source
    assert "kind: player.radioKind === 'video' ? 'video' : 'song'" in source
    assert "Radio: similar videos" in source
