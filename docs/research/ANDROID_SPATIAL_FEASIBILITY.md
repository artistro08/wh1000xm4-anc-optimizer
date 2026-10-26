# System-wide headphone spatialization on Android: feasibility

25 October 2026. Planning only; no device, Spotify or latency test was done. Official sources were read.

**Decision:** A standard rootless app cannot promise to force the audio of all apps, Spotify included, through its own DSP chain. First evaluate the phone's or OEM's existing spatializer; for custom DSP, source access and a single output path must be verified.

## API limits

- **AudioPlaybackCapture (Android 10+):** Copies audio. It needs `RECORD_AUDIO`, a user-approved `MediaProjection`, the same user profile, a suitable usage type and `ALLOW_CAPTURE_BY_ALL` on the source; the most restrictive policy applies. Capture permission cannot be forced on the source app. The API does not offer taking over or muting the source output. Replaying captured audio therefore causes double audio if the source is still heard; muting media volume may also mute the processed output and is not a solution. Spotify's current capture policy and protected-content behaviour were not verified here. DRM presence alone does not settle the question; this API is not a way to decrypt or bypass DRM. [Android playback capture](https://developer.android.com/media/platform/av-capture)
- **AudioEffect:** Attaches to a specific `AudioTrack`/`MediaPlayer` session ID. Using an insert effect on the global mix through session `0` is deprecated. That does not mean it fails on every device, but it is not a portable system-wide architecture. Existing effect control does not grant arbitrary PCM capture or the right to insert a custom HRTF engine. [AudioEffect](https://developer.android.com/reference/android/media/audiofx/AudioEffect)
- **Spatializer:** Queries the device's optional spatializer support, state and format compatibility; support can change with the connected headphone. The public app API does not hand other apps' PCM to a custom DSP. Using the NDK/C++ does not lift the capture policy either; native DSP must be planned to run only on audio it can reach. [Spatializer](https://developer.android.com/reference/android/media/Spatializer), [Android capture guide including native capture policy](https://developer.android.com/media/platform/av-capture)

## Possible paths

| Path | Scope and condition |
| --- | --- |
| The phone's existing OEM spatializer | Lowest development cost; depends on the target phone, XM4 output and stereo content support. No custom HRTF control assumed. |
| Our own player + native DSP | Works for PCM we control and can decode. Has no system-wide effect on the Spotify app itself. |
| Rootless capture–process–replay | A candidate only if the source allows capture and a way is proven to reduce the source to a single output without breaking capture. Otherwise the product path is dropped. |
| OEM/system effect integration | AOSP device effects need a vendor library, `audio_effects.xml` and system/HAL integration. Root/custom ROM is a separate, device-specific track at this layer; root alone does not guarantee success. |

The OEM requirements in the last row are described in the official documentation; the root/custom-ROM option is an engineering inference from it. [AOSP audio effects](https://source.android.com/docs/core/audio/audio-effects)

## Three verification gates before implementation

1. **Device and existing path:** Record the phone model, Android/OEM version, root status and XM4 connection type; record whether the existing spatializer works on the target stereo content.
2. **Source access:** Verify that permitted PCM capture or a usable effect session really exists for the target Spotify version and content; if not, stop the rootless custom-DSP path.
3. **Single processed output:** Verify that the original audio is not heard as well, that the processed output does not re-capture itself, and that the path survives a headphone reconnect. If this fails, choose OEM / own player / a separate root investigation.

These gates are future feasibility checks; no test or latency measurement was done in this work.
