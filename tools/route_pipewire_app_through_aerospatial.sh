#!/usr/bin/env bash
# Temporarily route audio through AeroSpatial while preserving EasyEffects EQ.
# Target topology: EasyEffects (EQ) -> AeroSpatial -> physical sink (no parallel output)
# If EasyEffects is absent, routes [node-name] directly: [node-name] -> AeroSpatial -> physical sink.
# Usage: bash tools/route_pipewire_app_through_aerospatial.sh [node-name] [daemon-options...]
# Examples:
#   bash tools/route_pipewire_app_through_aerospatial.sh spotify
#   bash tools/route_pipewire_app_through_aerospatial.sh spotify --azimuth 45
#   bash tools/route_pipewire_app_through_aerospatial.sh spotify --azimuth 60 --blend 0.85
set -u

app_node="spotify"
daemon_args=()
if [[ $# -gt 0 ]]; then
    app_node="$1"
    shift
    daemon_args=("$@")
fi
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
report_dir="$repo_root/aerospatial/build/live-smoke"
daemon="$repo_root/aerospatial/build/aerospatial-daemon"
mkdir -p "$report_dir"

if ! wpctl status >"$report_dir/app-route-before.txt" 2>"$report_dir/app-route-error.txt"; then
    cat "$report_dir/app-route-error.txt"
    printf 'PipeWire is unavailable in this session. Run from the logged-in desktop terminal.\n'
    exit 1
fi
if [[ ! -x "$daemon" ]]; then
    printf 'Missing daemon: %s\nBuild with: cmake --build aerospatial/build -j2\n' "$daemon"
    exit 1
fi

default_sink_info=$(wpctl inspect @DEFAULT_AUDIO_SINK@ 2>&1) || exit 1
physical_sink=$(sed -nE 's/.*node\.name = "([^"]+)".*/\1/p' <<<"$default_sink_info" | head -1)
if [[ -z "$physical_sink" || "$physical_sink" == aerospatial_3d_sink ]]; then
    printf 'Could not identify a physical default output device.\n'
    exit 1
fi

"$daemon" "${daemon_args[@]}" >"$report_dir/app-route-daemon.log" 2>&1 &
daemon_pid=$!
cleanup_done=0
declare -a old_left=()
declare -a old_right=()
route_started=0
ee_mode=0

get_targets() {
    local source_port=$1
    awk -v src="$source_port" '
        $0 == src { selected=1; next }
        /^[^[:space:]]/ { selected=0 }
        selected && /\|->/ { sub(/^.*\|-> /, ""); print }
    ' "$report_dir/app-route-current.txt"
}

cleanup() {
    [[ "$cleanup_done" == 1 ]] && return
    cleanup_done=1
    if [[ "$route_started" == 1 ]]; then
        if [[ "$ee_mode" == 1 ]]; then
            pw-link -d ee_soe_equalizer:output_FL aerospatial_3d_sink:playback_FL 2>/dev/null || true
            pw-link -d ee_soe_equalizer:output_FR aerospatial_3d_sink:playback_FR 2>/dev/null || true
            pw-link -d ee_soe_output_level:output_FL aerospatial_3d_sink:playback_FL 2>/dev/null || true
            pw-link -d ee_soe_output_level:output_FR aerospatial_3d_sink:playback_FR 2>/dev/null || true
            pw-link -d aerospatial_3d_sink:capture_FL "$physical_sink":playback_FL 2>/dev/null || true
            pw-link -d aerospatial_3d_sink:capture_FR "$physical_sink":playback_FR 2>/dev/null || true
            if [[ ${#old_left[@]} -eq 0 ]]; then
                pw-link "ee_soe_output_level:output_FL" "$physical_sink:playback_FL" 2>/dev/null || true
            else
                for target in "${old_left[@]}"; do
                    pw-link "ee_soe_output_level:output_FL" "$target" 2>/dev/null || true
                done
            fi
            if [[ ${#old_right[@]} -eq 0 ]]; then
                pw-link "ee_soe_output_level:output_FR" "$physical_sink:playback_FR" 2>/dev/null || true
            else
                for target in "${old_right[@]}"; do
                    pw-link "ee_soe_output_level:output_FR" "$target" 2>/dev/null || true
                done
            fi
            printf 'EasyEffects -> %s direct route restored; AeroSpatial stopped.\n' "$physical_sink"
        else
            pw-link -d "$app_node:output_FL" aerospatial_3d_sink:playback_FL 2>/dev/null || true
            pw-link -d "$app_node:output_FR" aerospatial_3d_sink:playback_FR 2>/dev/null || true
            pw-link -d aerospatial_3d_sink:capture_FL "$physical_sink":playback_FL 2>/dev/null || true
            pw-link -d aerospatial_3d_sink:capture_FR "$physical_sink":playback_FR 2>/dev/null || true
            for target in "${old_left[@]}"; do
                pw-link "$app_node:output_FL" "$target" 2>/dev/null || true
            done
            for target in "${old_right[@]}"; do
                pw-link "$app_node:output_FR" "$target" 2>/dev/null || true
            done
            printf 'Previous %s routing restored; AeroSpatial stopped.\n' "$app_node"
        fi
    fi
    kill "$daemon_pid" 2>/dev/null || true
    wait "$daemon_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

ready=0
for _ in {1..40}; do
    if ! kill -0 "$daemon_pid" 2>/dev/null; then break; fi
    if pw-link -i >"$report_dir/app-route-input-ports.txt" 2>&1 &&
       rg -q '^aerospatial_3d_sink:playback_FL$' "$report_dir/app-route-input-ports.txt"; then
        ready=1
        break
    fi
    sleep 0.1
done
if [[ "$ready" != 1 ]]; then
    printf 'AeroSpatial input ports did not appear. See %s/app-route-daemon.log\n' "$report_dir"
    exit 2
fi

pw-link -l >"$report_dir/app-route-current.txt" 2>&1 || exit 4

# Check whether EasyEffects equalizer node exists in PipeWire
if pw-link -o 2>/dev/null | grep -q '^ee_soe_equalizer:output_FL$'; then
    ee_mode=1
fi

# Connect AeroSpatial outputs to physical sink (the sole physical feeder)
pw-link aerospatial_3d_sink:capture_FL "$physical_sink":playback_FL || exit 4
pw-link aerospatial_3d_sink:capture_FR "$physical_sink":playback_FR || exit 4

wpctl status >"$report_dir/app-route-sink-status.txt" 2>&1 || exit 4
aero_id=$(sed -nE 's/.*[[:space:]]([0-9]+)\. AeroSpatial Stereo Virtual Speakers.*/\1/p' \
    "$report_dir/app-route-sink-status.txt" | head -1)
if [[ -z "$aero_id" ]]; then
    printf 'Could not find the temporary AeroSpatial sink ID in wpctl output.\n'
    exit 4
fi
# Set unity gain (1.0 = 0 dB) so we do not artificially attenuate the stream
wpctl set-volume "$aero_id" 1.0 || exit 4

if [[ "$ee_mode" == 1 ]]; then
    printf 'Detected active EasyEffects equalizer node.\n'
    printf 'Establishing chain: %s -> EasyEffects Pure EQ -> AeroSpatial -> %s (bypassing redundant internal BS2B crossfeed).\n' "$app_node" "$physical_sink"

    # Ensure app node is connected to EasyEffects sink
    if pw-link -o 2>/dev/null | grep -q "^$app_node:output_FL$"; then
        pw-link "$app_node:output_FL" easyeffects_sink:playback_FL 2>/dev/null || true
        pw-link "$app_node:output_FR" easyeffects_sink:playback_FR 2>/dev/null || true
    fi

    mapfile -t old_left < <(get_targets "ee_soe_output_level:output_FL")
    mapfile -t old_right < <(get_targets "ee_soe_output_level:output_FR")
    printf '%s\n' "${old_left[@]/#/ee_soe_output_level:output_FL|}" "${old_right[@]/#/ee_soe_output_level:output_FR|}" \
        >"$report_dir/app-route-restore.txt"

    route_started=1
    for target in "${old_left[@]}"; do
        pw-link -d "ee_soe_output_level:output_FL" "$target" || exit 5
    done
    for target in "${old_right[@]}"; do
        pw-link -d "ee_soe_output_level:output_FR" "$target" || exit 5
    done
    # Connect directly from pure equalizer node to avoid cascading conflicting crossfeeds
    pw-link "ee_soe_equalizer:output_FL" aerospatial_3d_sink:playback_FL || exit 5
    pw-link "ee_soe_equalizer:output_FR" aerospatial_3d_sink:playback_FR || exit 5
else
    printf 'EasyEffects physical link not detected; routing app node (%s) directly.\n' "$app_node"
    pw-link -o >"$report_dir/app-route-output-ports.txt" 2>&1 || exit 2
    for channel in FL FR; do
        if ! rg -Fxq "$app_node:output_$channel" "$report_dir/app-route-output-ports.txt"; then
            printf 'Missing PipeWire source port: %s:output_%s\n' "$app_node" "$channel"
            printf 'Available outputs are in %s/app-route-output-ports.txt\n' "$report_dir"
            exit 3
        fi
    done

    mapfile -t old_left < <(get_targets "$app_node:output_FL")
    mapfile -t old_right < <(get_targets "$app_node:output_FR")
    printf '%s\n' "${old_left[@]/#/$app_node:output_FL|}" "${old_right[@]/#/$app_node:output_FR|}" \
        >"$report_dir/app-route-restore.txt"

    route_started=1
    for target in "${old_left[@]}"; do
        pw-link -d "$app_node:output_FL" "$target" || exit 5
    done
    for target in "${old_right[@]}"; do
        pw-link -d "$app_node:output_FR" "$target" || exit 5
    done
    pw-link "$app_node:output_FL" aerospatial_3d_sink:playback_FL || exit 5
    pw-link "$app_node:output_FR" aerospatial_3d_sink:playback_FR || exit 5
fi

pw-link -l >"$report_dir/app-route-active.txt" 2>&1 || exit 5
printf 'AeroSpatial is active with preserved EQ and single physical output: %s.\n' "$physical_sink"
printf 'Press Ctrl+C to restore original routing and stop AeroSpatial.\n'
wait "$daemon_pid"
