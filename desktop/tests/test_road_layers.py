from pathlib import Path
import shutil
import subprocess

import pytest


def test_road_map_runtime():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the map runtime check')
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([node, str(Path(__file__).with_name('road_layers.cjs'))],
                            cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
