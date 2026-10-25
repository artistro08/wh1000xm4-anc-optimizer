"""Biquad IIR Filter Synthesizer for Sony WH-1000XM4 CXD90050 ANC Optimization.

Synthesizes Robert Bristow-Johnson (RBJ) 2nd-order Low-Shelf, Peaking EQ,
and Subsonic High-Pass filters across standard audio sample rates (48k, 96k, 192k, 768k).
Implements dual-biquad cascades and 5-stage CXD90050 hardware alignment block padding.
"""

from __future__ import annotations

import cmath
from dataclasses import dataclass
import math
from typing import List, Sequence, Tuple, TYPE_CHECKING, Union

import numpy as np

from xm4_optimizer.dsp.biquad import BiquadCoefficients

if TYPE_CHECKING:
    from xm4_optimizer.nvdm.cxd90050 import CXD90050AlignmentBlock

SUPPORTED_SAMPLE_RATES: Tuple[int, ...] = (48000, 96000, 192000, 768000)
DEFAULT_SAMPLE_RATE: int = 48000

# Recommended Acoustic ANC Boost Presets
DEFAULT_SHELF_F0: float = 1400.0  # Corner frequency in Hz
DEFAULT_BOOST_GAIN_DB: float = 7.5  # Nominal feedforward isolation gain boost in dB
DEFAULT_SHELF_SLOPE: float = 0.9  # Slope parameter S (smooth transition, zero overshoot)
TARGET_BAND_DEFAULT: Tuple[float, float] = (50.0, 800.0)

# Safe Feedforward Isolation Gain Bounds
ATTENUATION_MIN_REQUIRED_DB: float = 6.0
ATTENUATION_MAX_ALLOWED_DB: float = 9.0

# Subsonic Rumble Protection Defaults
SUBSONIC_HPF_DEFAULT_F0: float = 25.0  # Cutoff frequency in Hz
SUBSONIC_HPF_DEFAULT_Q: float = 0.7071067811865475  # 1/sqrt(2) Butterworth
CXD90050_BIQUAD_COUNT: int = 5  # Exactly 5 biquads per hardware alignment block


@dataclass
class SynthesisResult:
    """Result of ANC biquad filter synthesis.

    Matches interface contract between dsp, safety, and cli subsystems.
    """

    coefficients: List[BiquadCoefficients]
    poles: List[complex]
    is_stable: bool
    freqs: np.ndarray
    magnitude_db: np.ndarray
    band_min_gain_db: float
    band_max_gain_db: float

    @property
    def max_pole_magnitude(self) -> float:
        """Returns the maximum pole magnitude across all cascaded stages."""
        if not self.poles:
            return 0.0
        return max(abs(p) for p in self.poles)


def create_identity_biquad() -> BiquadCoefficients:
    """Returns an identity pass-through biquad (H(z) = 1.0)."""
    return BiquadCoefficients(a1=0.0, a2=0.0, b0=1.0, b1=0.0, b2=0.0)


def calculate_biquad_poles(coef: BiquadCoefficients) -> Tuple[complex, complex]:
    """Calculates the two discrete-time poles of a second-order section:

    P(z) = z^2 + a1*z + a2 = 0
    p1,2 = (-a1 +/- sqrt(a1^2 - 4*a2)) / 2
    """
    std = coef.to_standard()
    disc = std.a1 * std.a1 - 4.0 * std.a2
    sqrt_disc = cmath.sqrt(disc)
    p1 = (-std.a1 + sqrt_disc) / 2.0
    p2 = (-std.a1 - sqrt_disc) / 2.0
    return p1, p2


def is_biquad_stable(coef: BiquadCoefficients) -> bool:
    """Checks Schur-Cohn / Jury stability: |poles| < 1.0.

    Equivalent to (|a2| < 1.0) and (|a1| < 1.0 + a2).
    """
    p1, p2 = calculate_biquad_poles(coef)
    return abs(p1) < 1.0 and abs(p2) < 1.0


def pad_biquad_cascade(
    stages: Sequence[BiquadCoefficients],
    target_count: int = CXD90050_BIQUAD_COUNT,
) -> List[BiquadCoefficients]:
    """Pads a sequence of biquads to exactly target_count (5) using identity biquads.

    Raises:
        ValueError: If len(stages) exceeds target_count.
    """
    if len(stages) > target_count:
        raise ValueError(
            f"Cascade stage count ({len(stages)}) exceeds CXD90050 maximum ({target_count})"
        )
    result = list(stages)
    while len(result) < target_count:
        result.append(create_identity_biquad())
    return result


def create_alignment_block(
    filter_id: int,
    stages: Sequence[BiquadCoefficients],
    gain_db: float = 0.0,
) -> CXD90050AlignmentBlock:
    """Creates a 108-byte CXD90050AlignmentBlock populated with synthesized stages."""
    from xm4_optimizer.nvdm.cxd90050 import CXD90050AlignmentBlock

    padded = pad_biquad_cascade(stages, CXD90050_BIQUAD_COUNT)
    return CXD90050AlignmentBlock(filter_id=filter_id, gain_db=gain_db, biquads=padded)


def evaluate_cascade_response(
    stages: Sequence[BiquadCoefficients],
    freqs: Sequence[float],
    sample_rate: int,
) -> List[float]:
    """Computes cumulative magnitude response in dB across a frequency grid."""
    magnitudes: List[float] = []
    for f in freqs:
        w = 2.0 * math.pi * f / sample_rate
        ejw = cmath.exp(-1j * w)
        ej2w = cmath.exp(-2j * w)
        h_tot = 1.0 + 0j
        for b in stages:
            std = b.to_standard()
            num = std.b0 + std.b1 * ejw + std.b2 * ej2w
            den = 1.0 + std.a1 * ejw + std.a2 * ej2w
            if abs(den) < 1e-15:
                den = 1e-15 + 0j
            h_tot *= (num / den)
        mag_db = 20.0 * math.log10(max(abs(h_tot), 1e-15))
        magnitudes.append(mag_db)
    return magnitudes


def synthesize_low_shelf(
    f0: float = DEFAULT_SHELF_F0,
    gain_db: float = DEFAULT_BOOST_GAIN_DB,
    slope: float = DEFAULT_SHELF_SLOPE,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> BiquadCoefficients:
    """Synthesizes a 2nd-order RBJ Low-Shelf filter.

    Args:
        f0: Corner frequency in Hz (0 < f0 < sample_rate/2).
        gain_db: Low frequency shelf boost in dB.
        slope: Shelf slope parameter S (typically 0.9 to 1.0).
        sample_rate: Sampling frequency in Hz.

    Returns:
        Normalized BiquadCoefficients in standard form.

    Raises:
        ValueError: On non-positive frequencies, Nyquist violation, or non-positive slope.
    """
    if f0 <= 0:
        raise ValueError(f"Corner frequency f0 must be positive, got {f0}")
    if sample_rate <= 0:
        raise ValueError(f"Sample rate must be positive, got {sample_rate}")
    if f0 >= sample_rate / 2.0:
        raise ValueError(f"f0 ({f0} Hz) must be strictly less than Nyquist ({sample_rate / 2.0} Hz)")
    if slope <= 0:
        raise ValueError(f"Shelf slope must be positive, got {slope}")

    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * f0 / sample_rate
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)

    term = (A + 1.0 / A) * (1.0 / slope - 1.0) + 2.0
    if term < 0.0:
        raise ValueError(f"Slope {slope} and gain {gain_db} dB produce non-positive radicand {term}")
    alpha = (sin_w0 / 2.0) * math.sqrt(term)
    beta = 2.0 * math.sqrt(A) * alpha

    b0 = A * ((A + 1.0) - (A - 1.0) * cos_w0 + beta)
    b1 = 2.0 * A * ((A - 1.0) - (A + 1.0) * cos_w0)
    b2 = A * ((A + 1.0) - (A - 1.0) * cos_w0 - beta)
    a0 = (A + 1.0) + (A - 1.0) * cos_w0 + beta
    a1 = -2.0 * ((A - 1.0) + (A + 1.0) * cos_w0)
    a2 = (A + 1.0) + (A - 1.0) * cos_w0 - beta

    return BiquadCoefficients(
        a1=a1 / a0,
        a2=a2 / a0,
        b0=b0 / a0,
        b1=b1 / a0,
        b2=b2 / a0,
    )


def synthesize_peaking_eq(
    f0: float,
    gain_db: float,
    q: float = 1.0,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> BiquadCoefficients:
    """Synthesizes a 2nd-order RBJ Peaking EQ filter.

    Args:
        f0: Center frequency in Hz (0 < f0 < sample_rate/2).
        gain_db: Peak gain or cut in dB.
        q: Quality factor Q (f0 / delta_f).
        sample_rate: Sampling frequency in Hz.

    Returns:
        Normalized BiquadCoefficients in standard form.
    """
    if f0 <= 0:
        raise ValueError(f"Center frequency f0 must be positive, got {f0}")
    if sample_rate <= 0:
        raise ValueError(f"Sample rate must be positive, got {sample_rate}")
    if f0 >= sample_rate / 2.0:
        raise ValueError(f"f0 ({f0} Hz) must be strictly less than Nyquist ({sample_rate / 2.0} Hz)")
    if q <= 0:
        raise ValueError(f"Quality factor q must be positive, got {q}")

    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * f0 / sample_rate
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    b0 = 1.0 + alpha * A
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / A

    return BiquadCoefficients(
        a1=a1 / a0,
        a2=a2 / a0,
        b0=b0 / a0,
        b1=b1 / a0,
        b2=b2 / a0,
    )


def synthesize_high_pass(
    f0: float = SUBSONIC_HPF_DEFAULT_F0,
    q: float = SUBSONIC_HPF_DEFAULT_Q,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> BiquadCoefficients:
    """Synthesizes a 2nd-order RBJ High-Pass filter for subsonic protection.

    Args:
        f0: Cutoff corner frequency in Hz (e.g. 25 Hz).
        q: Quality factor Q (default 0.7071 Butterworth).
        sample_rate: Sampling frequency in Hz.
    """
    if f0 <= 0:
        raise ValueError(f"Corner frequency f0 must be positive, got {f0}")
    if sample_rate <= 0:
        raise ValueError(f"Sample rate must be positive, got {sample_rate}")
    if f0 >= sample_rate / 2.0:
        raise ValueError(f"f0 ({f0} Hz) must be strictly less than Nyquist ({sample_rate / 2.0} Hz)")
    if q <= 0:
        raise ValueError(f"Quality factor q must be positive, got {q}")

    w0 = 2.0 * math.pi * f0 / sample_rate
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2.0 * q)

    b0 = (1.0 + cos_w0) / 2.0
    b1 = -(1.0 + cos_w0)
    b2 = (1.0 + cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha

    return BiquadCoefficients(
        a1=a1 / a0,
        a2=a2 / a0,
        b0=b0 / a0,
        b1=b1 / a0,
        b2=b2 / a0,
    )


def synthesize_anc_boost(
    target_band: Tuple[float, float] = TARGET_BAND_DEFAULT,
    boost_gain_db: float = DEFAULT_BOOST_GAIN_DB,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    shelf_f0: float = DEFAULT_SHELF_F0,
    shelf_slope: float = DEFAULT_SHELF_SLOPE,
    cascade_subsonic: bool = False,
    subsonic_f0: float = SUBSONIC_HPF_DEFAULT_F0,
) -> SynthesisResult:
    """Primary synthesis entrypoint: synthesizes ANC boost filter and verifies stability.

    Args:
        target_band: (f_min, f_max) frequency band for attenuation assertion (50-800 Hz).
        boost_gain_db: Low frequency boost gain in dB (nominally +7.5 dB).
        sample_rate: Target sampling rate (48000, 96000, 192000, 768000).
        shelf_f0: Low-shelf transition corner frequency (nominally 1400 Hz).
        shelf_slope: Low-shelf slope S (nominally 0.9).
        cascade_subsonic: If True, cascades a 25 Hz 2nd-order Butterworth HPF.
        subsonic_f0: Cutoff frequency for subsonic HPF.

    Returns:
        SynthesisResult with coefficients, poles, stability flag, frequency grid, and metrics.
    """
    stages: List[BiquadCoefficients] = [
        synthesize_low_shelf(
            f0=shelf_f0,
            gain_db=boost_gain_db,
            slope=shelf_slope,
            sample_rate=sample_rate,
        )
    ]
    if cascade_subsonic:
        stages.append(
            synthesize_high_pass(
                f0=subsonic_f0,
                sample_rate=sample_rate,
            )
        )

    # Calculate all discrete-time poles
    poles: List[complex] = []
    for b in stages:
        p1, p2 = calculate_biquad_poles(b)
        poles.extend([p1, p2])

    is_stable = all(abs(p) < 1.0 for p in poles)

    # Evaluate in-band frequency response
    f_low, f_high = target_band
    num_band_points = 100
    band_freqs = [f_low + i * (f_high - f_low) / (num_band_points - 1) for i in range(num_band_points)]
    band_mags = evaluate_cascade_response(stages, band_freqs, sample_rate)

    band_min = float(min(band_mags))
    band_max = float(max(band_mags))

    # Evaluate full logarithmic grid (20 Hz to min(20 kHz, Nyquist - 10 Hz))
    num_full_points = 200
    log_start = math.log10(20.0)
    nyquist = sample_rate / 2.0
    f_max_grid = min(20000.0, nyquist - 10.0)
    log_end = math.log10(f_max_grid)
    full_freqs = np.array([
        10.0 ** (log_start + i * (log_end - log_start) / (num_full_points - 1))
        for i in range(num_full_points)
    ], dtype=np.float64)
    full_mags = np.array(
        evaluate_cascade_response(stages, full_freqs, sample_rate),
        dtype=np.float64
    )

    return SynthesisResult(
        coefficients=stages,
        poles=poles,
        is_stable=is_stable,
        freqs=full_freqs,
        magnitude_db=full_mags,
        band_min_gain_db=band_min,
        band_max_gain_db=band_max,
    )


def synthesize_anc_filter(
    target_gain_db: float = DEFAULT_BOOST_GAIN_DB,
    fs: float = float(DEFAULT_SAMPLE_RATE),
    filter_id: int = 0,
    base_block: Any = None,
) -> Tuple[CXD90050AlignmentBlock, SynthesisResult]:
    """High-level synthesizer returning (CXD90050AlignmentBlock, SynthesisResult).

    Enforces target_gain_db within [+6.0 dB, +9.0 dB] safe feedforward bounds.
    """
    if not (ATTENUATION_MIN_REQUIRED_DB <= target_gain_db <= ATTENUATION_MAX_ALLOWED_DB):
        raise ValueError(
            f"target_gain_db ({target_gain_db} dB) must be within "
            f"[{ATTENUATION_MIN_REQUIRED_DB}, {ATTENUATION_MAX_ALLOWED_DB}] dB"
        )
    res = synthesize_anc_boost(
        target_band=TARGET_BAND_DEFAULT,
        boost_gain_db=target_gain_db,
        sample_rate=int(fs),
        cascade_subsonic=True,
    )
    block = create_alignment_block(
        filter_id=filter_id,
        stages=res.coefficients,
        gain_db=target_gain_db,
    )
    return block, res
