import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).parent


def import_in_directory(module_name, directory):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(PROJECT_ROOT), env.get("PYTHONPATH")))
    )
    return subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
    )


def test_importing_config_does_not_create_engine_toml(tmp_path):
    result = import_in_directory("config", tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "engine.toml").exists()


def test_importing_ingester_does_not_create_engine_toml(tmp_path):
    result = import_in_directory("ingester", tmp_path)

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "engine.toml").exists()
