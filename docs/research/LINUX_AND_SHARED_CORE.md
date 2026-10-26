# Linux and the shared DSP core: research note

Date: 2026-10-25. Scope: source review and architecture plan; no new installation or audio device test.

## Recommendation

The first working target is Linux/PipeWire. Applications send audio to the AeroSpatial virtual output; the DSP output is linked to an explicitly chosen physical headphone. Input/output routing differs from merely recording app audio and replaying it; double audio and loops must be prevented. Changing the default device alone does not prove that all existing app streams moved.

PipeWire's official filter-chain module supports virtual sinks and capture/playback streams. The SOFA spatializer option exists in builds compiled with `libmysofa`; it can be a useful comparison path for our own engine. Keeping the existing C++ engine in the product path makes it easier to keep processing behaviour identical across platforms. This is a design proposal, not the result of a comparative quality test. [PipeWire filter-chain](https://docs.pipewire.org/page_module_filter_chain.html)

Steam Audio's official sources state Windows, Linux and Android support and the Apache-2.0 licence. So the first candidate is the existing Steam Audio backend; writing a convolution engine from scratch should not be the first goal. The library licence does not grant redistribution rights for separately downloaded HRTF datasets. [Steam Audio](https://github.com/ValveSoftware/steam-audio)

SOFA support does not cover all SOFA files: the engine requires `SimpleFreeFieldHRIR` and further field constraints. Check these limits before loading; an incompatible file should fail with a clear error. [Steam Audio custom HRTFs](https://github.com/ValveSoftware/steam-audio/blob/master/core/doc/guide.rst)

## Work items from the code (state of 2026-10-25)

- `aerospatial/CMakeLists.txt` puts `PipeWireBridge.cpp` into the core and links PipeWire unconditionally. Platform-independent core, Linux adapter and offline test runner should be separate targets. The download script also supports only Linux x64.
- `processStereo` and `processPointSource` padded short frames with zeros and dropped the remaining output. A stateful input/output FIFO or an equivalent block adapter was needed. **Update 2026-10-26:** a ring-buffer FIFO and `test_variable_quantum_continuity` were added.
- `SpatialConfig.mode` and `enableEarlyReflections` are not used in the current processing path. `setOrbitalCoordinates` updates fields that the stereo path does not read. These must not be listed as ready features.
- Parameter updates from a UI/control thread need safe transfer and smoothing. HRTF loading must happen outside the audio callback.
- Virtual sink ports exist; the bridge does not yet implement explicit target device selection, automatic linking and a reconnect policy. A node appearing in `wpctl` is not end-to-end audio evidence.

## First acceptance checks

1. Processing the same input with 64/128/256/480/512 and mixed block sizes gives the same sample sequence as a fixed-block reference once aligned; tails and filter ends are preserved.
2. Selected streams from Spotify, a browser and a local player reach the headphone through one processed path; bypass, device change and daemon shutdown are checked.
3. 44.1/48 kHz, mono/stereo, input silence, invalid SOFA and source change have defined behaviour. Offline tests need neither PipeWire nor an audio device.

The latency benchmark is out of scope at the user's request. Block continuity and routing checks are basic correctness checks, not latency tests.
