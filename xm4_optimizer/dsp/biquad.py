"""Second-order recursive IIR biquad section model, filtering engines, and sign normalization.

Provides:
- SignConvention enum (STANDARD vs MAC_PRE_NEGATED)
- BiquadCoefficients dataclass with Q28/Q31 serialization, poles, and stability checks
- BiquadFilter stateful Transposed Direct Form II processing engine
- BiquadCascade serial multi-stage filter chain
- detect_sign_convention auto-detection utility
"""

from __future__ import annotations

import cmath
from dataclasses import dataclass
from enum import Enum
import math
import struct
from typing import List, Optional, Sequence, Tuple

from xm4_optimizer.dsp.fixed_point import (
    BIQUAD_BYTE_SIZE,
    BIQUAD_STRUCT_FORMAT,
    float_to_q28,
    float_to_q31,
    pack_biquad_q28,
    q28_to_float,
    q31_to_float,
    unpack_biquad_q28,
)


class SignConvention(str, Enum):
    """Sign convention for biquad denominator coefficients."""

    STANDARD = "standard"  # y[n] = sum(b_k*x) - a1*y[n-1] - a2*y[n-2], D(z) = 1 + a1*z^-1 + a2*z^-2
    MAC_PRE_NEGATED = "mac"  # y[n] = sum(b_k*x) + a1'*y[n-1] + a2'*y[n-2], where a1' = -a1, a2' = -a2


def detect_sign_convention(a1: float, a2: float) -> SignConvention:
    """Heuristically detects sign convention from denominator coefficients.

    For low-frequency audio filters (50 Hz - 800 Hz) where pole real parts are positive:
    - Standard form: a1 < 0 and a2 > 0
    - MAC pre-negated: a1 > 0 and a2 < 0
    """
    if a1 > 0.0 and a2 < 0.0:
        return SignConvention.MAC_PRE_NEGATED
    return SignConvention.STANDARD


@dataclass
class BiquadCoefficients:
    """5-coefficient second-order IIR section.

    H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)  [Standard form]

    Attributes:
        a1: Denominator coefficient z^-1 (normalized to a0=1.0).
        a2: Denominator coefficient z^-2.
        b0: Numerator coefficient z^0 (feedthrough gain, default 1.0).
        b1: Numerator coefficient z^-1.
        b2: Numerator coefficient z^-2.
        sign_convention: Sign convention in effect (STANDARD or MAC_PRE_NEGATED).
    """

    a1: float = 0.0
    a2: float = 0.0
    b0: float = 1.0  # Pass-through identity default
    b1: float = 0.0
    b2: float = 0.0
    sign_convention: SignConvention = SignConvention.STANDARD

    @classmethod
    def passthrough(cls) -> BiquadCoefficients:
        """Returns a unity pass-through biquad section (b0=1.0, all others 0.0)."""
        return cls(a1=0.0, a2=0.0, b0=1.0, b1=0.0, b2=0.0)

    def to_standard(self) -> BiquadCoefficients:
        """Returns a copy normalized to STANDARD sign convention."""
        if self.sign_convention == SignConvention.STANDARD:
            return BiquadCoefficients(
                a1=self.a1,
                a2=self.a2,
                b0=self.b0,
                b1=self.b1,
                b2=self.b2,
                sign_convention=SignConvention.STANDARD,
            )
        return BiquadCoefficients(
            a1=-self.a1,
            a2=-self.a2,
            b0=self.b0,
            b1=self.b1,
            b2=self.b2,
            sign_convention=SignConvention.STANDARD,
        )

    def to_mac(self) -> BiquadCoefficients:
        """Returns a copy normalized to MAC_PRE_NEGATED sign convention."""
        if self.sign_convention == SignConvention.MAC_PRE_NEGATED:
            return BiquadCoefficients(
                a1=self.a1,
                a2=self.a2,
                b0=self.b0,
                b1=self.b1,
                b2=self.b2,
                sign_convention=SignConvention.MAC_PRE_NEGATED,
            )
        return BiquadCoefficients(
            a1=-self.a1,
            a2=-self.a2,
            b0=self.b0,
            b1=self.b1,
            b2=self.b2,
            sign_convention=SignConvention.MAC_PRE_NEGATED,
        )

    def to_sign_convention(self, target: SignConvention) -> BiquadCoefficients:
        """Converts coefficients to the target sign convention."""
        if target == SignConvention.STANDARD:
            return self.to_standard()
        return self.to_mac()

    def to_q28_tuple(self) -> Tuple[int, int, int, int, int]:
        """Returns (a1, a2, b0, b1, b2) packed as 32-bit Q28 integers."""
        return (
            float_to_q28(self.a1),
            float_to_q28(self.a2),
            float_to_q28(self.b0),
            float_to_q28(self.b1),
            float_to_q28(self.b2),
        )

    @classmethod
    def from_q28_tuple(
        cls,
        q_tuple: Tuple[int, int, int, int, int],
        sign_convention: SignConvention = SignConvention.STANDARD,
    ) -> BiquadCoefficients:
        """Instantiates BiquadCoefficients from a tuple of 5 Q28 integers."""
        return cls(
            a1=q28_to_float(q_tuple[0]),
            a2=q28_to_float(q_tuple[1]),
            b0=q28_to_float(q_tuple[2]),
            b1=q28_to_float(q_tuple[3]),
            b2=q28_to_float(q_tuple[4]),
            sign_convention=sign_convention,
        )

    def to_q31_tuple(self, prescale: int = 2) -> Tuple[int, int, int, int, int]:
        """Returns (a1, a2, b0, b1, b2) packed as 32-bit Q31 integers with prescaling."""
        return (
            float_to_q31(self.a1, prescale=prescale),
            float_to_q31(self.a2, prescale=prescale),
            float_to_q31(self.b0, prescale=prescale),
            float_to_q31(self.b1, prescale=prescale),
            float_to_q31(self.b2, prescale=prescale),
        )

    @classmethod
    def from_q31_tuple(
        cls,
        q_tuple: Tuple[int, int, int, int, int],
        prescale: int = 2,
        sign_convention: SignConvention = SignConvention.STANDARD,
    ) -> BiquadCoefficients:
        """Instantiates BiquadCoefficients from a tuple of 5 Q31 integers."""
        return cls(
            a1=q31_to_float(q_tuple[0], prescale=prescale),
            a2=q31_to_float(q_tuple[1], prescale=prescale),
            b0=q31_to_float(q_tuple[2], prescale=prescale),
            b1=q31_to_float(q_tuple[3], prescale=prescale),
            b2=q31_to_float(q_tuple[4], prescale=prescale),
            sign_convention=sign_convention,
        )

    def pack(self) -> bytes:
        """Serializes biquad into 20 bytes (<5i in order [a1, a2, b0, b1, b2])."""
        return struct.pack(BIQUAD_STRUCT_FORMAT, *self.to_q28_tuple())

    @classmethod
    def unpack(
        cls,
        data: bytes,
        sign_convention: SignConvention = SignConvention.STANDARD,
    ) -> BiquadCoefficients:
        """Deserializes biquad from 20 bytes."""
        if len(data) != BIQUAD_BYTE_SIZE:
            raise ValueError(
                f"Biquad packed size must be {BIQUAD_BYTE_SIZE} bytes, got {len(data)}"
            )
        q_tuple = struct.unpack(BIQUAD_STRUCT_FORMAT, data)
        return cls.from_q28_tuple(q_tuple, sign_convention=sign_convention)

    def poles(self) -> List[complex]:
        """Computes roots of the characteristic polynomial z^2 + a1*z + a2 = 0.

        Always normalizes to STANDARD convention before computing poles.
        """
        std = self.to_standard()
        disc = std.a1 * std.a1 - 4.0 * std.a2
        sqrt_disc = cmath.sqrt(disc)
        p1 = (-std.a1 + sqrt_disc) / 2.0
        p2 = (-std.a1 - sqrt_disc) / 2.0
        return [p1, p2]

    def is_stable(self, tol: float = 1e-12) -> bool:
        """Checks whether all poles reside strictly inside the open unit circle (|p| < 1.0)."""
        p_mags = [abs(p) for p in self.poles()]
        return all(mag < (1.0 - tol) for mag in p_mags)

    def stability_margin(self) -> float:
        """Computes the Schur-Cohn / Jury stability margin delta.

        delta = min(1 - |a2|, 1 + a2 - |a1|). Filter is strictly stable iff delta > 0.
        """
        std = self.to_standard()
        cond1 = 1.0 - abs(std.a2)
        cond2 = 1.0 + std.a2 - abs(std.a1)
        return min(cond1, cond2)

    def response(self, f: float, fs: float) -> complex:
        """Computes complex frequency response H(e^jw) at frequency f (Hz) given sample rate fs (Hz)."""
        w = 2.0 * math.pi * f / fs
        ejw = cmath.exp(-1j * w)
        ej2w = cmath.exp(-2j * w)
        std = self.to_standard()
        num = std.b0 + std.b1 * ejw + std.b2 * ej2w
        den = 1.0 + std.a1 * ejw + std.a2 * ej2w
        if abs(den) < 1e-15:
            raise ZeroDivisionError(f"Transfer function pole at frequency {f} Hz")
        return num / den

    def magnitude_db(self, f: float, fs: float) -> float:
        """Evaluates magnitude in dB: 20 * log10(|H(e^jw)|)."""
        h = self.response(f, fs)
        return 20.0 * math.log10(abs(h))

    def phase_rad(self, f: float, fs: float) -> float:
        """Evaluates phase in radians: arg(H(e^jw))."""
        h = self.response(f, fs)
        return cmath.phase(h)


class BiquadFilter:
    """Stateful Transposed Direct Form II recursive biquad filter engine.

    Supports sample-by-sample and array/sequence processing.
    """

    def __init__(self, coefficients: BiquadCoefficients) -> None:
        self.coefficients = coefficients
        self.s1: float = 0.0
        self.s2: float = 0.0

    def reset(self) -> None:
        """Clears internal filter states s1 and s2."""
        self.s1 = 0.0
        self.s2 = 0.0

    def process_sample(self, x: float) -> float:
        """Processes a single input sample x and updates internal states."""
        c = self.coefficients
        y = c.b0 * x + self.s1
        if c.sign_convention == SignConvention.STANDARD:
            self.s1 = c.b1 * x - c.a1 * y + self.s2
            self.s2 = c.b2 * x - c.a2 * y
        else:  # MAC_PRE_NEGATED
            self.s1 = c.b1 * x + c.a1 * y + self.s2
            self.s2 = c.b2 * x + c.a2 * y
        return y

    def process(self, x: Sequence[float]) -> List[float]:
        """Filters an input sequence of audio samples."""
        return [self.process_sample(s) for s in x]


class BiquadCascade:
    """Serial cascade of N BiquadFilter stages."""

    def __init__(self, filters: Sequence[BiquadFilter]) -> None:
        if not filters:
            raise ValueError("BiquadCascade must contain at least one filter stage")
        self.filters = list(filters)

    def reset(self) -> None:
        """Resets all cascaded stages."""
        for f in self.filters:
            f.reset()

    def process_sample(self, x: float) -> float:
        """Passes sample sequentially through all stages."""
        val = x
        for f in self.filters:
            val = f.process_sample(val)
        return val

    def process(self, x: Sequence[float]) -> List[float]:
        """Filters an input sequence through all cascaded stages."""
        return [self.process_sample(s) for s in x]
