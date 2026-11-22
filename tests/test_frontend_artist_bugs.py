import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_frontend_artist_bug_regressions():
    node = shutil.which("node")
    if node is None:
        if os.getenv("CI"):
            pytest.fail("node is required in CI")
        pytest.skip("node is not installed")
    tests = [
        ROOT / "tests" / "js" / "artist-profile-ux.test.js",
        ROOT / "tests" / "js" / "artist-links.test.js",
        ROOT / "tests" / "js" / "up-next-layout.test.js",
        ROOT / "tests" / "js" / "track-row-layout.test.js",
    ]
    result = subprocess.run(
        [node, "--test", *(str(path) for path in tests)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
