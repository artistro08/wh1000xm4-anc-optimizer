# Binaural processing of Windows system audio: feasibility

Date: 2026-10-25. Scope: Spotify and apps that play to the chosen output; desktop Windows. Four primary documents were reviewed. This is an architecture assessment; nothing was verified on Windows and no latency was measured.

**Decision:** For the first Windows check, investigate applying a fixed binaural filter/profile through the existing Equalizer APO. Do not commit to direct integration of the shared C++ core until its plugin interface and licence are verified. A virtual audio device + WASAPI bridge is the second option for a standalone product; new driver development is not part of the first stage.

| Path | Flow and benefit | Cost / limits |
|---|---|---|
| Virtual render endpoint + WASAPI | Apps → virtual output → capture → shared C++ binaural DSP → physical headphone. The DSP stays in a user process and is easy to share with Linux/Android. | The virtual device driver, installation and distribution are needed separately. If source and physical output clocks differ, buffering/resampling is needed. Apps that target the physical output directly skip the processing. |
| Custom APO | The C++ core is wrapped in a Windows COM/APO adapter and placed in the audio processing graph. No separate capture/replay bridge. | Real-time rules, INF/device association and installation are required. Each endpoint and processing mode must be verified separately. |
| Equalizer APO integration | Shipping profiles/filters on top of an existing APO installation can avoid new driver work in the first test. | The right device must be selected in the Configurator; a restart is required after installation. OEM APO conflicts and disabled enhancements can block processing. The available documentation does not confirm a direct C++ plugin contract. |

APOs are real-time COM objects that run in user mode. SFX sit per stream, MFX per mode, EFX at endpoint level. RAW streams can skip some SFX/MFX paths; Microsoft's description of EFX coverage for RAW does not guarantee support for every alternative audio API. A custom APO can be added to the audio graph through an INF. [Microsoft APO architecture](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-processing-object-architecture)

**Loopback alone does not replace the audio.** WASAPI copies the audio going to the physical output into a capture buffer; it does not cut the original. Processing the capture and sending it to the same output can cause double audio and a re-capture loop. In the proposed virtual-device design, the original goes only to the virtual output and the processed audio plays on a different physical output. Loopback supports shared mode only; no universal capture can be promised for exclusive streams and protected content. Trusted drivers can block capture of protected digital content. The name Spotify alone does not prove a specific protection path is used. [Microsoft loopback recording](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)

In the first release, "system-wide" should be defined as **verified shared-mode apps routed to the chosen endpoint**. Exclusive/ASIO and protected paths stay outside acceptance; the same behaviour must not be assumed for all of them. Equalizer APO needs to coexist with OEM effects and requires enhancements to be enabled; disabling OEM effects can remove existing features. [Equalizer APO installation and troubleshooting](https://sourceforge.net/p/equalizerapo/wiki/Documentation/)

**Distribution and licence gate:** Production signing of a custom virtual driver is a separate work package; a local development installation is not product distribution. For attestation or WHCP submission, Microsoft requires at least one valid EV certificate associated with the dashboard account; submission signing certificate and SHA-2 requirements apply as well. This document alone does not determine which certification path suffices for a given package. [Microsoft driver code signing requirements](https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/code-signing-reqs)

The Equalizer APO usage documentation reviewed does not confirm the exact licence version or redistribution terms. Do not package anything until the source licence of the chosen version, any plugin SDK licence, redistribution rights for a virtual driver and the licence of the HRTF/BRIR data are recorded with version pins. Do not assume GPL compatibility or commercial redistribution rights. Sharing configuration with independently installed software and combining code/binaries need separate review.

**Gates before implementation:**

1. On Windows with the XM4, verify that the effect is really active in Spotify and browser shared-mode audio, that bypass returns, and that there is no double audio.
2. Verify safe recovery at 44.1/48 kHz, mono/stereo, device removal, Bluetooth reconnect and default device change.
3. Make the support limit visible for exclusive, RAW, enhancements-off and protected streams; never silently show "processing".
4. Keep platform APIs out of the shared C++ DSP; block processing, format conversion and parameter updates live in adapters. No allocation, file access or blocking locks in the audio callback.
5. For Equalizer APO, treat feasibility with a fixed filter and dynamic core integration as separate decisions. If a direct integration contract cannot be verified, move to the virtual endpoint bridge.
6. Verify clean install/uninstall, endpoint association after restart, signing, and component/data licences. Latency measurement is outside this research.
