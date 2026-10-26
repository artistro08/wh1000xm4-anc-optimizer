# Linux trials with stock PipeWire components

The first implementation is [stereo-virtual-speakers.conf](stereo-virtual-speakers.conf). It uses PipeWire's own `filter-chain` module, the `libmysofa` spatializer and the MIT KEMAR SOFA file installed on the system. It places the left and right stereo channels on separate virtual front speakers at ±30° and mixes the four ear signals in two mixers. There is no extra C++ convolution code. This is stereo music virtualization; it does not create independent sound objects that the recording does not contain.

The required files were present on the development machine:

```text
/usr/lib/pipewire-0.3/libpipewire-module-filter-chain.so
/usr/lib/spa-0.2/filter-graph/libspa-filter-graph-plugin-sofa.so
/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa
```

[probe_sofa_hrtf.cpp](probe_sofa_hrtf.cpp) uses the `libmysofa` API to check the SOFA data without a live audio session:

```sh
c++ -std=c++17 aerospatial/pipewire/probe_sofa_hrtf.cpp $(pkg-config --cflags --libs libmysofa) -o /tmp/probe_sofa_hrtf
/tmp/probe_sofa_hrtf
```

On the development machine a 558-sample filter loaded at 48 kHz. For +30° the left/right filter energy was 2.386/0.341; for 330° it was 0.341/2.386, so the left/right change is present in the data. `delay_L/R = 0` must not be read as "no ITD"; the delay can be inside the filter samples. This check does not show that sound is heard on the headphone.

## Live desktop trial

For a short live trial of the C++ engine that uses the Steam Audio C API (`phonon.h`), with the headphone as the real default output, run from the repository root:

```sh
bash tools/run_linux_steam_audio_smoke.sh
```

The script opens a temporary virtual node, links its outputs to the current default physical device, plays a left–center–right tone sequence and saves the PipeWire links during playback to `aerospatial/build/live-smoke/`. The center part is fed in phase to both channels; it tests the phantom-center image of the two virtual speakers. Do not use `sudo`; run the command in a terminal of the desktop session the headphone is connected to. This is a horizontal stereo virtualization test; it does not test elevation.

## Routing one application through AeroSpatial

To route a PipeWire application stream (Spotify by default) through AeroSpatial temporarily:

```sh
bash tools/route_pipewire_app_through_aerospatial.sh
```

The script saves the application's existing links and restores them on Ctrl+C. Another application's PipeWire node name can be given as the first argument. If EasyEffects is running, the script now feeds AeroSpatial from the EasyEffects equalizer output (`ee_soe_equalizer:output_FL/FR`), so the EQ stays in the chain, and it sets the virtual sink to unity volume.

History: in the first live Spotify trial the user reported muffled sound, the previous EQ not being applied and the image collapsing to the center. The script at that time detached Spotify from EasyEffects, which explains the EQ loss. The center image and timbre problems are not confirmed solved: a new live listening test is still pending. This stage is recorded as a failed listening acceptance and the script is not recommended for daily use yet.

The PipeWire SOFA trial below is a separate comparison path using stock system components.

1. Confirm with `wpctl status` that the real headphone appears in PipeWire. Make the real headphone the default output; do not make the virtual output the default yet.
2. In a terminal at the repository root, run:

   ```sh
   pipewire -c "$PWD/aerospatial/pipewire/stereo-virtual-speakers.conf"
   ```

   The virtual output exists only while this process runs; no user configuration file is installed. Find **AeroSpatial Stereo Virtual Speakers** with `wpctl status`. Check with `pw-link -l` that the `effect_output.aerospatial_stereo_sofa` output goes to the real headphone. If it goes elsewhere, do not play audio until the link is fixed.
3. Route a short file to the virtual output:

   ```sh
   pw-play --target effect_input.aerospatial_stereo_sofa /path/to/stereo-test.wav
   ```

   You should hear a single sound. `pw-link -l` should show player → virtual input → real headphone; there must be no link from the virtual output back to its own input. A file-based channel comparison can be done later with `pw-record` or other tools.
4. If this works, move the Spotify stream to this virtual output and repeat the checks. Moving only Spotify instead of changing the system default lets you test without affecting other applications. `wpctl status` shows the app stream and the chosen output.
5. When done, stop the virtual output with `Ctrl+C` in the first terminal. If needed, select the real headphone as the default output again.

## Evidence from the trial session

- The Steam Audio based C++ target was rebuilt and the offline DSP tests passed. This does not verify the PipeWire stream.
- The stock PipeWire module, the SOFA plugin and the KEMAR file were found on disk. The configuration could be read with `pw-config`.
- The `libmysofa` two-direction filter read above passed; the directions do not give the same result at the opposite ear.
- In the link graph saved for the smoke test, `pw-play` was linked to the AeroSpatial inputs and AeroSpatial to the WH-1000XM4. But that run used an old WAV file name, and the `wpctl` output level showed `0.00`. The file name and the sink-ID parsing (broken by a Unicode line prefix) were fixed later; the corrected left–center–right signal has not been verified live yet.
- The live Spotify trial is user listening evidence: muffled sound, EasyEffects EQ lost, center image narrowed. Without a system audio recording and a level-matched A/B measurement, the root causes of the muffling and center collapse are not proven.

For the next trial, compare EQ-only, spatial-only, EQ→spatial and direct bypass chains with the same source and the same output level. Record that each test source has exactly one physical output path in the PipeWire graph. Latency measurement is deferred.

Source: [PipeWire filter-chain and SOFA spatializer](https://docs.pipewire.org/page_module_filter_chain.html).
