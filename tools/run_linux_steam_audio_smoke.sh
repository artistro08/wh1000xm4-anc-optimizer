#!/usr/bin/env bash
# Run from the logged-in desktop session; never with sudo.
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
report_dir="$repo_root/aerospatial/build/live-smoke"
daemon="$repo_root/aerospatial/build/aerospatial-daemon"
mkdir -p "$report_dir"

if ! wpctl status >"$report_dir/wpctl-before.txt" 2>"$report_dir/wpctl-error.txt"; then
    cat "$report_dir/wpctl-error.txt"
    printf 'PipeWire is unavailable in this session. Run this in a desktop terminal without sudo.\n'
    exit 1
fi
if [[ ! -x "$daemon" ]]; then
    printf 'Missing daemon: %s\nBuild with: cmake --build aerospatial/build -j2\n' "$daemon"
    exit 1
fi

printf 'Current output devices:\n'
rg -n -A 18 'Sinks:' "$report_dir/wpctl-before.txt" | head -21 || true
printf '\nStarting the Steam Audio PipeWire node...\n'
"$daemon" >"$report_dir/daemon.log" 2>&1 &
daemon_pid=$!
cleanup() {
    kill "$daemon_pid" 2>/dev/null || true
    wait "$daemon_pid" 2>/dev/null || true
}
trap cleanup EXIT

ready=0
for _ in {1..30}; do
    if ! kill -0 "$daemon_pid" 2>/dev/null; then
        break
    fi
    if pw-cli ls Node >"$report_dir/nodes.txt" 2>"$report_dir/pw-cli-error.txt" &&
       rg -q 'aerospatial_3d_sink' "$report_dir/nodes.txt"; then
        ready=1
        break
    fi
    sleep 0.1
done

if [[ "$ready" != 1 ]]; then
    printf 'The Steam Audio node did not register. See %s/daemon.log\n' "$report_dir"
    tail -16 "$report_dir/daemon.log"
    exit 2
fi

default_sink_info=$(wpctl inspect @DEFAULT_AUDIO_SINK@ 2>&1) || {
    printf 'Could not inspect the current default output device.\n'
    exit 3
}
physical_sink=$(sed -nE 's/.*node\.name = "([^"]+)".*/\1/p' <<<"$default_sink_info" | head -1)
if [[ -z "$physical_sink" || "$physical_sink" == aerospatial_3d_sink ]]; then
    printf 'The default output is not a physical sink; refusing to create an uncertain route.\n'
    exit 3
fi

# A filter's output ports are not auto-connected to the physical sink.
pw-link -o >"$report_dir/ports-output.txt" 2>&1 || true
pw-link -i >"$report_dir/ports-input.txt" 2>&1 || true
if ! rg -q 'aerospatial_3d_sink:capture_FL' "$report_dir/ports-output.txt" ||
   ! rg -q 'aerospatial_3d_sink:capture_FR' "$report_dir/ports-output.txt"; then
    printf 'Steam Audio output ports were not exposed by PipeWire. See %s/ports-output.txt\n' "$report_dir"
    exit 4
fi
pw-link aerospatial_3d_sink:capture_FL "$physical_sink":playback_FL || exit 4
pw-link aerospatial_3d_sink:capture_FR "$physical_sink":playback_FR || exit 4
wpctl status >"$report_dir/wpctl-active.txt" 2>&1 || true
pw-link -l >"$report_dir/links-active.txt" 2>&1 || true
if ! rg -q 'aerospatial_3d_sink:capture_FL' "$report_dir/links-active.txt" ||
   ! rg -q 'aerospatial_3d_sink:capture_FR' "$report_dir/links-active.txt"; then
    printf 'Could not verify both AeroSpatial-to-output links. See %s/links-active.txt\n' "$report_dir"
    exit 4
fi

# Set the temporary virtual sink to unity gain (1.0 = 0 dB)
aero_id=$(sed -nE 's/.*[[:space:]]([0-9]+)\. AeroSpatial Stereo Virtual Speakers.*/\1/p' "$report_dir/wpctl-active.txt" | head -1)
if [[ -z "$aero_id" ]]; then
    printf 'Could not find the temporary AeroSpatial sink ID in wpctl output.\n'
    exit 4
fi
wpctl set-volume "$aero_id" 1.0 || exit 4
printf 'Node registered. Generating a quiet left-center-right stereo test.\n'

python3 - "$report_dir/test-left-center-right.wav" <<'PY'
import math
from pathlib import Path
import struct
import sys
import wave

path = Path(sys.argv[1])
rate = 48000
tone_samples = int(rate * 0.85)
pause_samples = int(rate * 0.25)
segments = ("left", "center", "right")
with wave.open(str(path), "wb") as output:
    output.setnchannels(2)
    output.setsampwidth(2)
    output.setframerate(rate)
    samples = bytearray()
    for index, position in enumerate(segments):
        for n in range(tone_samples):
            amp = int(5000 * math.sin(2 * math.pi * 440 * n / rate))
            left = amp if position in ("left", "center") else 0
            right = amp if position in ("center", "right") else 0
            samples.extend(struct.pack("<hh", left, right))
        if index != len(segments) - 1:
            samples.extend(bytes(pause_samples * 4))
    output.writeframes(samples)
PY

pw-play --volume 0.3 --target aerospatial_3d_sink \
    "$report_dir/test-left-center-right.wav" >"$report_dir/play.log" 2>&1 &
play_pid=$!
sleep 0.4
# WirePlumber may also auto-connect pw-play to the default sink. Move the
# stream explicitly to the virtual sink so the physical path cannot bypass DSP.
pw-link -d pw-play:output_FL "$physical_sink":playback_FL || true
pw-link -d pw-play:output_FR "$physical_sink":playback_FR || true
pw-link pw-play:output_FL aerospatial_3d_sink:playback_FL || exit 5
pw-link pw-play:output_FR aerospatial_3d_sink:playback_FR || exit 5
pw-link -l >"$report_dir/links-during.txt" 2>&1 || true
wait "$play_pid"
play_status=$?
pw-link -l >"$report_dir/links-after.txt" 2>&1 || true
wpctl status >"$report_dir/wpctl-after.txt" 2>&1 || true

printf 'Playback exit code: %s\n' "$play_status"
printf 'Report directory: %s\n' "$report_dir"
printf 'Expected: left virtual speaker, phantom center, right virtual speaker.\n'
if rg -Uq 'aerospatial_3d_sink:playback_FL\n[[:space:]]*\|<- pw-play:output_FL' "$report_dir/links-during.txt" &&
   rg -Uq 'aerospatial_3d_sink:playback_FR\n[[:space:]]*\|<- pw-play:output_FR' "$report_dir/links-during.txt"; then
    printf 'Verified: test playback entered both AeroSpatial inputs.\n'
else
    printf 'Route check failed. Review %s/links-during.txt\n' "$report_dir"
    exit 5
fi
if rg -Uq 'bluez_output[^\n]*:playback_FL\n[[:space:]]*\|<- pw-play:output_FL' "$report_dir/links-during.txt" ||
   rg -Uq 'bluez_output[^\n]*:playback_FR\n[[:space:]]*\|<- pw-play:output_FR' "$report_dir/links-during.txt"; then
    printf 'Direct playback link to the headphones is still present.\n'
    exit 6
fi
if [[ "$play_status" != 0 ]]; then
    cat "$report_dir/play.log"
fi
exit "$play_status"
