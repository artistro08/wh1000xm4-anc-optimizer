"""Sony CXD90050 (QN1) DSP and DNC Parameter Data Models.

Defines dataclasses and binary serialization for:
- CXD90050AlignmentBlock (ALIGN_FF_L, ALIGN_FF_R, ALIGN_FB_L, ALIGN_FB_R - 108 bytes)
- HowlingPreventionParam (HOWLING_PRE_PARAM - 32 bytes)
- HowlingDetectionParam (HOWLING_DET_PARAM - 24 bytes)
- OptimizerParam (OPTIMIZER_PARAM - 40 bytes)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
from typing import List, Tuple

# Q28 Fixed-Point Constants
Q28_SCALE: float = float(1 << 28)  # 268435456.0
Q28_MIN_INT: int = -(1 << 31)  # -2147483648
Q28_MAX_INT: int = (1 << 31) - 1  # 2147483647

# Filter ID enumeration
FILTER_ID_ALIGN_FF_L: int = 0
FILTER_ID_ALIGN_FF_R: int = 1
FILTER_ID_ALIGN_FB_L: int = 2
FILTER_ID_ALIGN_FB_R: int = 3

FILTER_NAME_TO_ID: dict[str, int] = {
    "ALIGN_FF_L": FILTER_ID_ALIGN_FF_L,
    "ALIGN_FF_R": FILTER_ID_ALIGN_FF_R,
    "ALIGN_FB_L": FILTER_ID_ALIGN_FB_L,
    "ALIGN_FB_R": FILTER_ID_ALIGN_FB_R,
}

FILTER_ID_TO_NAME: dict[int, str] = {v: k for k, v in FILTER_NAME_TO_ID.items()}


from xm4_optimizer.dsp.fixed_point import float_to_q28, q28_to_float
from xm4_optimizer.dsp.biquad import BiquadCoefficients


@dataclass
class CXD90050AlignmentBlock:
    """108-byte Sony CXD90050 Microphone Alignment Parameter Block.

    Binary Layout:
    - Header (8 bytes):
        uint32_t filter_id  (0=ALIGN_FF_L, 1=ALIGN_FF_R, 2=ALIGN_FB_L, 3=ALIGN_FB_R)
        float32_t gain_db   (alignment trim gain in dB)
    - Stages (100 bytes):
        5 cascaded Biquad sections x 20 bytes each (Q28 fixed-point)
    Total: 108 bytes
    """

    filter_id: int
    gain_db: float = 0.0
    biquads: List[BiquadCoefficients] = field(
        default_factory=lambda: [BiquadCoefficients() for _ in range(5)]
    )

    def __post_init__(self) -> None:
        if len(self.biquads) != 5:
            raise ValueError(
                f"CXD90050AlignmentBlock must contain exactly 5 biquads, got {len(self.biquads)}"
            )

    def pack(self) -> bytes:
        """Serializes alignment block to exactly 108 bytes."""
        header = struct.pack("<If", self.filter_id, self.gain_db)
        biquad_bytes = b"".join(b.pack() for b in self.biquads)
        assert len(header) + len(biquad_bytes) == 108
        return header + biquad_bytes

    @classmethod
    def unpack(cls, data: bytes) -> CXD90050AlignmentBlock:
        """Deserializes alignment block from exactly 108 bytes."""
        if len(data) != 108:
            raise ValueError(
                f"CXD90050AlignmentBlock size must be 108 bytes, got {len(data)}"
            )
        filter_id, gain_db = struct.unpack("<If", data[:8])
        biquads = []
        for stage_idx in range(5):
            offset = 8 + (stage_idx * 20)
            biquads.append(BiquadCoefficients.unpack(data[offset : offset + 20]))
        return cls(filter_id=filter_id, gain_db=gain_db, biquads=biquads)


def serialize_alignment_block(block: CXD90050AlignmentBlock) -> bytes:
    """Serializes alignment block to 108 bytes with Q28 fixed-point coefficients."""
    return block.pack()


def deserialize_alignment_block(data: bytes) -> CXD90050AlignmentBlock:
    """Deserializes alignment block from 108 bytes."""
    return CXD90050AlignmentBlock.unpack(data)


@dataclass
class HowlingPreventionParam:
    """32-byte DNC Howling Prevention Control Parameter Block (HOWLING_PRE_PARAM).

    Binary Layout (<I7f):
    - uint32_t enable             (1 = enabled, 0 = disabled)
    - float32_t max_attenuation_db(e.g., -12.0 dB)
    - float32_t attack_time_ms    (e.g., 5.0 ms)
    - float32_t release_time_ms   (e.g., 50.0 ms)
    - float32_t notch_freq_low_hz (e.g., 1000.0 Hz)
    - float32_t notch_freq_high_hz(e.g., 8000.0 Hz)
    - float32_t notch_q           (e.g., 4.0)
    - float32_t stability_margin_db(e.g., 3.0 dB)
    """

    enable: int = 1
    max_attenuation_db: float = -12.0
    attack_time_ms: float = 5.0
    release_time_ms: float = 50.0
    notch_freq_low_hz: float = 1000.0
    notch_freq_high_hz: float = 8000.0
    notch_q: float = 4.0
    stability_margin_db: float = 3.0

    def pack(self) -> bytes:
        """Serializes howling prevention parameters to exactly 32 bytes."""
        return struct.pack(
            "<I7f",
            self.enable,
            self.max_attenuation_db,
            self.attack_time_ms,
            self.release_time_ms,
            self.notch_freq_low_hz,
            self.notch_freq_high_hz,
            self.notch_q,
            self.stability_margin_db,
        )

    @classmethod
    def unpack(cls, data: bytes) -> HowlingPreventionParam:
        """Deserializes howling prevention parameters from exactly 32 bytes."""
        if len(data) != 32:
            raise ValueError(f"HowlingPreventionParam must be 32 bytes, got {len(data)}")
        fields = struct.unpack("<I7f", data)
        return cls(*fields)


@dataclass
class HowlingDetectionParam:
    """24-byte DNC Howling Detection Control Parameter Block (HOWLING_DET_PARAM).

    Binary Layout (<fII3f):
    - float32_t threshold_dbfs    (e.g., -18.0 dBFS)
    - uint32_t persistence_frames (e.g., 8 frames)
    - uint32_t window_size        (e.g., 512 samples)
    - float32_t tonality_ratio    (e.g., 0.85)
    - float32_t feedback_coherence(e.g., 0.90)
    - float32_t hold_time_ms      (e.g., 200.0 ms)
    """

    threshold_dbfs: float = -18.0
    persistence_frames: int = 8
    window_size: int = 512
    tonality_ratio: float = 0.85
    feedback_coherence: float = 0.90
    hold_time_ms: float = 200.0

    def pack(self) -> bytes:
        """Serializes howling detection parameters to exactly 24 bytes."""
        return struct.pack(
            "<fII3f",
            self.threshold_dbfs,
            self.persistence_frames,
            self.window_size,
            self.tonality_ratio,
            self.feedback_coherence,
            self.hold_time_ms,
        )

    @classmethod
    def unpack(cls, data: bytes) -> HowlingDetectionParam:
        """Deserializes howling detection parameters from exactly 24 bytes."""
        if len(data) != 24:
            raise ValueError(f"HowlingDetectionParam must be 24 bytes, got {len(data)}")
        fields = struct.unpack("<fII3f", data)
        return cls(*fields)


@dataclass
class OptimizerParam:
    """40-byte Personal NC Optimizer Parameter Block (OPTIMIZER_PARAM).

    Binary Layout (<I3fI4fI):
    - uint32_t calibration_state       (0=Uncalibrated, 1=Calibrated, 2=Optimizing)
    - float32_t atmospheric_pressure_hpa(e.g., 1013.25 hPa)
    - float32_t pressure_trim_db       (e.g., 0.0 dB)
    - float32_t seal_score             (0.0 to 1.0, e.g., 0.95)
    - uint32_t glasses_detected        (0=No, 1=Yes)
    - float32_t leakage_damping        (e.g., 1.2)
    - float32_t canal_gain_l_db        (e.g., +1.5 dB)
    - float32_t canal_gain_r_db        (e.g., +1.5 dB)
    - float32_t target_ff_boost_db     (e.g., +7.5 dB)
    - uint32_t timestamp               (Unix epoch timestamp)
    """

    calibration_state: int = 1
    atmospheric_pressure_hpa: float = 1013.25
    pressure_trim_db: float = 0.0
    seal_score: float = 0.95
    glasses_detected: int = 0
    leakage_damping: float = 1.2
    canal_gain_l_db: float = 1.5
    canal_gain_r_db: float = 1.5
    target_ff_boost_db: float = 7.5
    timestamp: int = 1600000000

    def pack(self) -> bytes:
        """Serializes optimizer parameters to exactly 40 bytes."""
        return struct.pack(
            "<I3fI4fI",
            self.calibration_state,
            self.atmospheric_pressure_hpa,
            self.pressure_trim_db,
            self.seal_score,
            self.glasses_detected,
            self.leakage_damping,
            self.canal_gain_l_db,
            self.canal_gain_r_db,
            self.target_ff_boost_db,
            self.timestamp,
        )

    @classmethod
    def unpack(cls, data: bytes) -> OptimizerParam:
        """Deserializes optimizer parameters from exactly 40 bytes."""
        if len(data) != 40:
            raise ValueError(f"OptimizerParam must be 40 bytes, got {len(data)}")
        fields = struct.unpack("<I3fI4fI", data)
        return cls(*fields)
