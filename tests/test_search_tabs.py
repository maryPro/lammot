import os
import re
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


def test_video_badge_sits_left_of_the_duration():
    source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    badge = source.index("class: 'track-badge'")
    duration = source.index("class: 'track-dur'")
    assert badge < duration


def test_clearing_search_resets_the_tab_to_songs():
    source = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    match = re.search(r"function clearSearch\(\) \{\n(.*?)\n  \}", source, re.S)
    assert match, "clearSearch() not found in app.js"
    body = match.group(1)
    reset = "SearchTabs.normalizeTab('songs')"
    assert reset in body, (
        "Clearing the search field must reset state.searchType to songs; "
        "otherwise Extended stays selected although Songs is the default."
    )
    assert body.index(reset) < body.index("onSearchInput()"), (
        "The tab must be reset before re-rendering; otherwise the clear button "
        "leaves Extended visually selected."
    )


def test_search_tabs_are_centred():
    css = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
    assert "margin: var(--spacing-sm) auto 0;" in css
