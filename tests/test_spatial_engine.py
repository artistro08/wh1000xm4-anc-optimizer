import subprocess
import os
import pytest

def test_aerospatial_binary_exists():
    bin_path = "/home/randward/teamwork_projects/xm4_anc_optimizer/aerospatial/build/test_aerospatial"
    daemon_path = "/home/randward/teamwork_projects/xm4_anc_optimizer/aerospatial/build/aerospatial-daemon"
    assert os.path.exists(bin_path), f"Binary not found: {bin_path}"
    assert os.path.exists(daemon_path), f"Daemon not found: {daemon_path}"

def test_aerospatial_dsp_validation_execution():
    bin_path = "/home/randward/teamwork_projects/xm4_anc_optimizer/aerospatial/build/test_aerospatial"
    res = subprocess.run([bin_path], capture_output=True, text=True)
    assert res.returncode == 0, f"Validation failed with stderr: {res.stderr}"
    assert "OFFLINE DSP CHECKS PASSED!" in res.stdout
    assert "Acoustic ITD (625 us)" in res.stdout
    assert "IACC Peak:" in res.stdout

def test_aerospatial_daemon_cli():
    daemon_path = "/home/randward/teamwork_projects/xm4_anc_optimizer/aerospatial/build/aerospatial-daemon"
    res = subprocess.run([daemon_path, "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "AeroSpatial Stereo Virtual Speakers" in res.stdout
    assert "--sofa" in res.stdout
    assert "--azimuth" in res.stdout
