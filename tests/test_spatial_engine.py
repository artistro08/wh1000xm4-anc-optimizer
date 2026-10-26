from pathlib import Path
import subprocess
import os
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_PATH = REPO_ROOT / "aerospatial" / "build" / "test_aerospatial"
DAEMON_PATH = REPO_ROOT / "aerospatial" / "build" / "aerospatial-daemon"


def test_aerospatial_binary_exists():
    if not BIN_PATH.exists() or not DAEMON_PATH.exists():
        pytest.skip("AeroSpatial C++ binaries not built in this environment")
    assert BIN_PATH.exists(), f"Binary not found: {BIN_PATH}"
    assert DAEMON_PATH.exists(), f"Daemon not found: {DAEMON_PATH}"


def test_aerospatial_dsp_validation_execution():
    if not BIN_PATH.exists():
        pytest.skip("AeroSpatial C++ test binary not built in this environment")
    res = subprocess.run([str(BIN_PATH)], capture_output=True, text=True)
    assert res.returncode == 0, f"Validation failed with stderr: {res.stderr}"
    assert "OFFLINE DSP CHECKS PASSED!" in res.stdout
    assert "Acoustic ITD (625 us)" in res.stdout
    assert "IACC Peak:" in res.stdout


def test_aerospatial_daemon_cli():
    if not DAEMON_PATH.exists():
        pytest.skip("AeroSpatial daemon binary not built in this environment")
    res = subprocess.run([str(DAEMON_PATH), "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "AeroSpatial Stereo Virtual Speakers" in res.stdout
    assert "--sofa" in res.stdout
    assert "--azimuth" in res.stdout
