# Spatial headphone processing for stereo music: literature review

Scope: render Spotify-like two-channel system output binaurally through two virtual speakers. This review contains no implementation or latency measurement.

- Part 1 (2026-10-25): a limited first pass, three sources.
- Part 2 (2026-10-26): a wider pass on realism (externalization, HRTF choice, headphone EQ, head tracking, center coloration). Tags: **[read]** = the full text or the relevant section was opened; **[re-checked]** = the key numbers were re-checked against the source by the maintainer; **[secondary]** = only a summary or third-party page was available; **[inference]** = engineering reasoning, not a sourced result.

## Basic distinction

The proposed input contract is two-channel PCM. Assigning virtual speaker positions to L/R changes how the existing stereo mix is presented on headphones. It does not bring back the original instrument objects, height channels or producer-defined object positions. So the product name and UI should say "stereo virtualization"; object-based or true multichannel support needs its own design. This is a limit of the input information and the proposed architecture, not a result of the experiments below.

## Part 1: primary studies read on 2026-10-25

### 1. Jayaram, Kemelmacher-Shlizerman and Seitz (2023)

[HRTF Estimation in the Wild, arXiv:2311.03560](https://arxiv.org/abs/2311.03560), [DOI](https://doi.org/10.1145/3586183.3606782). The HTML full text was read for §3 method, §4 data/training, §5 results and §6 limitations.

In eight participants, personalized spectral HRTFs improved total angle/elevation error and front–back confusion compared with a generic HRTF; the azimuth difference was not significant. Phase/ITD came from the generic HRTF. The study uses a single static source and needs microphones at the ear-canal entrance and head orientation data. Direct applicability to a commercial headphone is not shown. It is not a test of music timbre or long-term music preference.

**Consequence:** Do not promise automatic personal HRTF estimation in the MVP. Leave room for user HRTF import and profile selection; do not assume one generic profile externalizes equally for everyone.

### 2. Hoppe, van de Par, Flanagin and Ewert (2024)

[Combined assessment of auditory distance perception and externalization, arXiv:2408.14198](https://arxiv.org/abs/2408.14198). Parts of the 44-page PDF were read: introduction (pp. 1–5), rotating sections of the method conditions (pp. 10–12), externalization results summary (pp. 22–23), the start of the discussion and the conclusions (pp. 27–28, 34–35).

In a virtual audio-visual experiment with ten listeners, reverberation and room size significantly affected externalization. There was a trend between head-tracking/render types, but the difference was not significant. Perceived distance and externalization were partly independent.

**Consequence:** "Further away" and "outside the head" are separate ratings. Head tracking is a valuable extension candidate; this study does not guarantee a universal benefit. Room contribution should be adjustable. No ideal early-reflection time, level or timbre cost for music can be derived from it.

### 3. Basha, Gupta and Sharma (2007)

[Stereo widening system using binaural cues for headphones, conference PDF](https://users.cecs.anu.edu.au/~ramtin/ICSPCS/ICSPCS%2707/papers/55.pdf). Only the introduction and the start of §II on the first page were read; results were not. The approach combines boosting the difference signal, crossfeed and early reflections, and names timbral coloration as an evaluation criterion. No DOI/arXiv record was confirmed. Not used as performance evidence.

**Consequence:** Stereo widening, speaker virtualization and externalization are different criteria. Explicitly modelling the binaural transfer of two virtual speakers is more auditable than automatically boosting the difference signal.

## Part 2: realism findings (2026-10-26)

### Head movement and externalization

- Brimijoin, Boyd and Akeroyd, *PLoS One* 2013, "The Contribution of Head Movement to the Externalization and Internalization of Sounds": world-fixed virtual sounds were externalized 65% of the time, head-fixed ones 20%. [read, re-checked] [PMC3846779](https://pmc.ncbi.nlm.nih.gov/articles/PMC3846779)
- Martin and Picinali, arXiv:2510.09161 (2025), "Impact of HRTF individualisation and head movements in a real/virtual localisation task": "using individual HRTFs improved perceived realism but not localisation performance in the static scenario"; "the opposite was observed when head movements were possible and encouraged." [read, re-checked] [arXiv:2510.09161](https://arxiv.org/abs/2510.09161)
- Begault, Wenzel and Anderson, JAES 49(10), 2001: head tracking, reverberation and individualized HRTFs had significant effects on localization error, reversals and externalization. Effect sizes not available. [secondary] [AES](https://aes2.org/publications/elibrary-page/?id=10175)

**Consequence [inference]:** For a listener without tracking, the HRTF fit and room cues matter most; head tracking is likely the single biggest improvement once available.

### Head-tracking latency

- Lindau, DAGA 2009, "The Perception of System Latency in Dynamic Binaural Synthesis": overall mean detection threshold 107.6 ms (σ 30.4 ms); the lowest threshold observed was 53 ms. [read, re-checked] [PDF](https://pub.dega-akustik.de/NAG_DAGA_2009/data/articles/000377.pdf)
- Martin and Picinali cite about 30 ms (Brungart) as a detectability threshold; their own system measured about 15.3 ms. [read]
- The Bluetooth (A2DP/LDAC) latency of the XM4 is not measured or sourced. With a phone IMU as tracker, audio lags head rotation by this amount. Target [inference]: well under 50 ms end to end, ideally under 30 ms.

### Room cues

- The literature above supports reverberation as an externalization cue, but no source gave a recommended direct-to-reverberant ratio or RT60 for music. [read/secondary]
- Starting point [inference]: per virtual speaker, early reflections plus a short diffuse tail (about 0.2–0.4 s), wet level about 12–18 dB below the direct sound, tuned by ear. These numbers are not sourced.

### HRTF sets (licences as stated on the source pages, 2026-10-26)

| Set | Contents | Licence | Source |
|---|---|---|---|
| SADIE II | 18 subjects + 2 dummy heads; HRIRs, BRIRs, headphone responses; SOFA | Apache-2.0 [read] | [Zenodo](https://zenodo.org/records/10886409) |
| HUTUBS (TU Berlin) | 96 subjects; HRIRs, head meshes, anthropometry, headphone transfer functions | CC BY 4.0 [read] | [DepositOnce](https://depositonce.tu-berlin.de/items/dc2a3076-a291-417e-97f0-7697e332c960/full) |
| CIPIC, ARI, IRCAM LISTEN | 45 / 250+ / 50 subjects | Unverified | [SOFA file list](https://www.sofaconventions.org/mediawiki/index.php/Files) |

Steam Audio accepts user HRTFs in SOFA format. [Steam Audio C API](https://valvesoftware.github.io/steam-audio/doc/capi/)

### Timbre: why the current render can sound muffled

- Steam Audio's `IPL_HRTFNORMTYPE_RMS` is documented as normalizing "HRTF volume to ensure similar volume from all directions based on root-mean-square value of each HRTF". It equalizes level between directions; it does not remove the direction-independent colour of the dummy head. [read] [phonon.h](https://raw.githubusercontent.com/ValveSoftware/steam-audio/master/core/src/core/phonon.h)
- Diffuse-field equalization removes the direction-independent part of an HRTF set and is used to improve high-frequency timbre in binaural rendering. [secondary] McKenzie, Murphy and Kearney, [Appl. Sci. 2018](https://www.mdpi.com/2076-3417/8/10/1956). The [Spatial Audio Framework](https://github.com/leomccormack/Spatial_Audio_Framework) includes a diffuse-field EQ routine. [read]
- Hypothesis [inference]: stacking an undiffused HRTF colour with a headphone target EQ (AutoEq/Harman) can over-correct. Test by A/B with the headphone EQ at 100%, 50% and 0% after diffuse-field EQ.

### Center notch from summing two HRTF pairs

- Hypothesis [inference]: identical L/R content rendered at ±θ reaches each ear through two different HRTFs; their pinna notches do not line up, and the sum forms a comb/notch. This fits the previously reported notch near 10 kHz at ±30° (not reproduced).
- Candidate fix [inference]: mid/side split. Render the side signal through the ±θ speakers and the mid signal through a single 0° source or a mild crossfeed path.
- Crossfeed reference: bs2b is a low-pass cross path that "does not add ambience or HRTF processing"; its default (700 Hz, 4.5 dB) is described as closest to virtual speakers at 30°. [read] [bs2b](https://bs2b.sourceforge.net/)
- Neural direct/ambient upmixing exists (DAFx 2024, HDemucs-based binaural upmixer, reported average azimuth error "less than 11.3 degrees"). [read] [DAFx24 paper 36](https://dafx.de/paper-archive/2024/papers/DAFx24_paper_36.pdf)

### Open-source references (licences as stated by the projects)

| Project | Licence | Relevance |
|---|---|---|
| [libmysofa](https://github.com/hoene/libmysofa) | BSD-3-Clause | SOFA reader |
| [Spatial Audio Framework](https://github.com/leomccormack/Spatial_Audio_Framework) | ISC (optional modules GPLv2) | HRTF interpolation, diffuse-field EQ |
| [SPARTA](https://github.com/leomccormack/SPARTA) | GPLv3 | Binauraliser with OSC head tracking; A/B reference |
| [3D Tune-In Toolkit](https://github.com/3DTune-In/3dti_AudioToolkit) | LGPL-3.0 | HRTF/BRIR rendering |
| [Resonance Audio](https://github.com/resonance-audio/resonance-audio) | Apache-2.0 (archived) | Reference |
| [JDSP4Linux](https://github.com/Audio4Linux/JDSP4Linux) | GPLv3 | Crossfeed, convolver |
| [opentrack](https://github.com/opentrack/opentrack) | mostly ISC | Head tracking over UDP |

## Prioritized changes for AeroSpatial [inference, grounded in the sources above]

1. Mid/side split to stop L=R content passing through two HRTF pairs. Check: L=R pink noise, 1/3-octave response before/after.
2. Diffuse-field EQ of the HRTF set (one fixed filter), then re-tune the headphone EQ amount.
3. Early reflections + short tail with an adjustable wet level.
4. Head tracking (yaw over OSC/UDP from a phone IMU or opentrack), latency budget under 50 ms.
5. Try SADIE II and HUTUBS subjects; keep the preferred one.
6. Ambient branch (decorrelated side at wide angles).
7. Fine-tune the virtual speaker angle between ±45° and ±60°. No source gives an optimal angle.

## Evaluation protocol

Level-match all conditions (within 0.5 dB). Play them in random order with hidden labels on the same 3–4 tracks (close-miked vocal, acoustic track, wide mix, near-mono content). Rate timbre, width and distance (inside → outside the head) separately; add a front/back judgement with a test noise. Repeat each trial at least three times and report only repeatable differences.

## Evidence limits

These sources do not show a successful result on the XM4 with Spotify music. Hendrickx 2017, Begault 2001 (reflection thresholds) and the McKenzie 2018 paper were not accessible in full and are cited only as secondary. Anthropometry-based HRTF selection and Apple/Sony 360RA internals were not verified.
