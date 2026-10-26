# Where does Sony 360 Reality Audio run for the XM4?

Date: 2026-10-26. Sources are Sony's official product and developer pages; the XM4 firmware binary was not disassembled for this note.

## Findings

- [Sony's 360RA developer page](https://www.sony.co.jp/en/Products/360RA/licensing/) lists a `Decoder/Renderer/Virtualizer` implementation for music services and receiving personalization parameters from the headphone companion app as separate requirements. The same page describes the certified-headphone side as personalization support. This does not support the claim that the WH-1000XM4 contains a hidden MPEG-H/360RA renderer that could be applied to any stereo recording.
- [Sony's creative workflow page](https://www.sony.co.jp/en/Products/create360RA/) says 360RA content is built from multichannel stems/object data, that an original object-based master cannot be produced from a stereo master alone, and that it can be heard on any headphone through a compatible smartphone service app. True producer object positions cannot be recovered from an ordinary stereo recording such as a Spotify track.
- The [WH-1000XM4 help guide](https://helpguide.sony.net/mdr/wh1000xm4/v1/en/contents/TP0002752840.html) lists SBC, AAC and LDAC for A2DP music. This alone does not prove the internal DSP lacks 3D capability, but it gives no evidence that a separate 360RA object stream goes from the phone to the headphone either.
- For the [WH-1000XM6](https://www.sony.com/en-bh/headphones/products/wh-1000xm6/developer-interview), Sony explicitly describes processing stereo in real time inside the headphone with "360 Upmix for Cinema". That is another product and feature; nothing shows it can be ported to the XM4 as a binary or algorithm.

## Decision

Keep the XM4's existing ANC/EQ processing path and the service-side 360RA renderer separate. Do not plan flash experiments on the assumption of a "firmware key that unlocks the feature". The shortest evidence path is to compare the codec/PCM output that a compatible service and an ordinary stereo app send to the headphone, and to look for concrete reverse-engineering evidence of an MPEG-H decoder/renderer or a 360RA enable command in the XM4 firmware. If such evidence turns up, the architecture can be revisited; if not, processing system audio through the open PipeWire/Steam Audio path is more practical.

Distributing a Sony 360RA implementation requires a format licence. MPEG-H being an open standard gives no access or redistribution rights to services' protected content or to Sony's application binaries. This is a licensing note; it does not mean independent stereo virtualization or the use of open HRTFs needs Sony's approval.
