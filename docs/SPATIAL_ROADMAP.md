# AeroSpatial Roadmap

26 October 2026: research, architecture plan and the first Linux listening trial. Latency measurement is deferred for now.

**Linux status (26 October):** A Steam Audio based C++ filter and per-app PipeWire routing were tried on the desktop. The user reported that Spotify playback sounded muffled, the existing EQ dropped out and the image collapsed to the center. The routing at the time removed Spotify's previous output links and sent audio straight to AeroSpatial, bypassing the EasyEffects EQ chain. That explains the EQ loss. The script was later changed to feed AeroSpatial from the EasyEffects equalizer output, and the engine got a block-size FIFO (see [STATUS.md](STATUS.md)). A new live listening test has not been done, so the Linux stage **stays open after a failed listening acceptance**, and the prototype is not ready for use. An earlier smoke test also had a wrong WAV path and a wrong virtual-sink volume mapping, so the earlier "left–center–right verified" statement is void. Details: [Linux notes](../aerospatial/pipewire/README.md).

## Goal and scope

The long-term goal is to render real multichannel or position-tagged audio from applications binaurally on headphones. The first Linux feasibility trial only places stereo PCM on two virtual front speakers and is called **stereo virtualization**. It cannot recover separate instrument positions or height objects that a stereo recording does not contain. Multichannel and object-based sources get their own acceptance gate. The existence of the prototype is not enough to announce "real 3D support".

The center of the code is a C++ processing core with no platform audio API dependencies. Linux, Windows and Android use separate routing adapters. The first convolution backend candidate is Valve Steam Audio. Switching to another backend must be justified by quality, licence or platform limits. Steam Audio supports Windows, Linux and Android. Its SOFA loader has format limits such as requiring `SimpleFreeFieldHRIR`. Sources: [Steam Audio](https://github.com/ValveSoftware/steam-audio), [custom HRTF limits](https://github.com/ValveSoftware/steam-audio/blob/master/core/doc/guide.rst).

## Order and acceptance gates

| Stage | Deliverable | Counts as done when |
|---|---|---|
| 0. Research | [Linux/core](research/LINUX_AND_SHARED_CORE.md), [Windows](research/WINDOWS_SPATIAL_FEASIBILITY.md), [Android](research/ANDROID_SPATIAL_FEASIBILITY.md), [literature](research/SPATIAL_AUDIO_LITERATURE.md) | Sources, limits and assumptions are visible in these notes. Done. |
| 1. Shared DSP | C++ library separated from PipeWire: stereo → two virtual speakers → binaural output, with bypass, blend and HRTF selection | Fixed and variable block sizes give the same output stream; filter tails are not lost; silence, mono/stereo and sample-rate behaviour are defined. Offline tests need no audio device. The block FIFO and a variable-quantum test exist (2026-10-26). |
| 2. Linux/PipeWire | A selectable AeroSpatial virtual output and a safe link to the chosen physical headphone | Spotify, a browser and a local player are heard once, processed; bypass, device removal/reconnect, daemon shutdown and loop prevention are checked. A node showing up in the list is not enough. [PipeWire filter-chain](https://docs.pipewire.org/page_module_filter_chain.html) |
| 3. Windows | First a fixed-effect feasibility test with Equalizer APO; then a choice between an APO and a virtual endpoint + WASAPI for the shared core | The chosen shared-mode endpoint is processed for Spotify and a browser; double audio, bypass, restart and Bluetooth reconnect are checked. If a driver is needed, signing and distribution are a separate task. [Microsoft APO](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-processing-object-architecture), [loopback](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording) |
| 4. Android feasibility | Rootless access experiments and a decision | On the target phone with the XM4: can Spotify be captured with permission, does the original still play as well, does the processed output get re-captured? If not, no rootless "custom DSP for all apps" promise; an OEM/system effect or a separate root/custom-ROM track is evaluated. [Android playback capture](https://developer.android.com/media/platform/av-capture), [AOSP effects](https://source.android.com/docs/core/audio/audio-effects) |
| 5. Listening quality | User-adjustable HRTF and virtual speaker angle; optional early reflections and personal SOFA import | In a level-matched comparison against bypass, timbre, externalization, front/back and center stability are judged separately. Dataset redistribution rights are checked. |

### Required work after the Linux trial

1. Keep the user's EQ in the graph: EasyEffects → AeroSpatial, or an equivalent documented EQ inside AeroSpatial. Old links must not be lost on route stop/restore. (Script updated; awaiting a live test.)
2. Separate the center collapse from the recording itself. `processStereo` binauralizes both channels to virtual speakers at ±azimuth and sums them; the Steam Audio default HRTF is used and a personal SOFA is not the default. Check channel mapping, phase/summing, frame/FIFO and the HRTF direction convention against the source and the official Steam Audio API docs. Candidate fixes from the literature review: mid/side split so L=R content does not pass through two HRTF pairs, and diffuse-field equalization of the HRTF set.
3. At low level and with a controlled source, compare bypass / EQ-only / spatial-only / EQ+spatial. Use the same source and output level, with exactly one physical output path per graph. Record the user's listening notes as evidence, but do not present a subjective result as measured externalization or "real 3D".
4. Do not move to global PipeWire routing, a GUI or Android until these are solved. Latency testing stays deferred.

## Concrete design for stage 1

`aerospatial_core` holds only PCM processing, parameters and the Steam Audio lifecycle. `PipeWireBridge` is a separate Linux target; an offline test runner is a third target. CMake and SDK paths are split per platform. The processing callback does no disk access, no blocking locks and no allocation. Control changes cross threads safely; HRTF loading happens outside the callback.

`SpatialConfig.mode`, `enableEarlyReflections` and the orbital parameters are not product features yet. They are either made to work or removed from the first public interface. "Sub-2 ms deterministic" and similar unmeasured claims must not be shown to users. Latency benchmarking is deferred, but stream continuity is a stage 1 gate.

## Platform decisions

**Linux:** The link between the PipeWire virtual sink and the physical headphone is managed explicitly. PipeWire's own SOFA spatializer can serve as a quality and feature baseline; the main application uses the same C++ core.

**Windows:** Equalizer APO is a short feasibility test. If the plugin path and licence for direct integration of the C++ core are not clear, switch to a standalone virtual endpoint + WASAPI bridge. In the first release "system-wide" means shared-mode streams on the chosen endpoint; exclusive/ASIO/protected paths are evaluated separately.

**Android:** Running C++ DSP through the NDK does not grant access to other apps' audio. Rootless `AudioPlaybackCapture` depends on the source app's policy and does not mute the original output. So Android stage 4 is a technical decision gate, not a delivery promise. A root/custom-ROM track is planned as a separate target if wanted. The call-routing idea (`sony-call-bridge`) is kept separate from the spatial work.

The broader Android goal is the base for an open companion app and the use of the shared DSP on audio that can be reached. App skeleton, device control and audio processing rights are designed as separate parts. No global 3D claim is made on Android before the Linux source → processing → headphone chain is verified.

## Evidence rules

- A research claim needs a direct source and the section that was read; a product claim needs a real application test. User observation, code tests and document claims are not mixed.
- Commit messages describe the technical change. Each stage is reviewed with a short research/implementation report and a concrete acceptance result. This roadmap is a research result, not a release or a claim of finished support on three platforms.
