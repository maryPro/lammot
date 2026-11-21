import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_artist_view_js():
    node = shutil.which("node")
    if node is None:
        if os.getenv("CI"):
            pytest.fail("node is required in CI")
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests" / "js" / "artist-view.test.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_index_loads_artist_view_before_app():
    html = _read("frontend/index.html")
    assert "/artist-view.js" in html
    assert html.index("/artist-view.js") < html.index("/app.js")


def test_app_has_artist_and_album_renderers():
    js = _read("frontend/app.js")
    assert "VIEW_RENDERERS.artist" in js
    assert "VIEW_RENDERERS.album" in js


def test_track_body_includes_artist_id():
    js = _read("frontend/app.js")
    match = re.search(r"function trackBody\(t\)\s*\{(.*?)\n  \}", js, re.S)
    assert match is not None, "trackBody not found"
    assert "artist_id" in match.group(1)


def test_fp_artist_is_a_button():
    html = _read("frontend/index.html")
    assert re.search(r"<button[^>]*id=\"fp-artist\"", html) is not None
