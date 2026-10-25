"""Discrete-Time Fourier Transform (DTFT) simulation and frequency response evaluation.

Provides:
- Frequency grid generation (logarithmic and linear)
- DTFT evaluation for single biquads, cascades, and CXD90050 alignment blocks
- Magnitude (dB), Phase (rad), and Linear gain computation
- Attenuation band verification (50 Hz - 800 Hz, +6 dB to +9 dB)
- ASCII frequency response curve renderer for CLI dry-run display
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, List, Optional, Sequence, Tuple, Union

import numpy as np


class AttenuationBandViolationError(ValueError):
    """Raised when simulated frequency response violates target attenuation band criteria."""

    def __init__(
        self,
        message: str,
        band: Tuple[float, float],
        min_gain_db: float,
        max_gain_db: float,
        required_min_db: float = 6.0,
        required_max_db: float = 9.0,
    ) -> None:
        super().__init__(message)
        self.band = band
        self.min_gain_db = min_gain_db
        self.max_gain_db = max_gain_db
        self.required_min_db = required_min_db
        self.required_max_db = required_max_db


@dataclass(frozen=True)
class BandVerificationResult:
    """Evaluation result for a specific frequency band assertion."""

    passed: bool
    band: Tuple[float, float]  # (f_low, f_high) in Hz
    min_gain_db: float  # Minimum observed gain in band
    max_gain_db: float  # Maximum observed gain in band
    mean_gain_db: float  # Average gain in band
    required_min_db: float  # e.g. +6.0 dB
    required_max_db: float  # e.g. +9.0 dB
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class FrequencyResponse:
    """Complete DTFT frequency response simulation results."""

    frequencies: np.ndarray  # Shape (N,), Hz
    complex_response: np.ndarray  # Shape (N,), complex128
    magnitude_linear: np.ndarray  # Shape (N,), float64
    magnitude_db: np.ndarray  # Shape (N,), float64
    phase_rad: np.ndarray  # Shape (N,), float64
    sample_rate: int  # e.g. 48000
    band_verification: Optional[BandVerificationResult] = None

    def get_gain_at(self, freq_hz: float) -> float:
        """Returns interpolated gain in dB at specified frequency."""
        return float(np.interp(freq_hz, self.frequencies, self.magnitude_db))


def create_frequency_grid(
    start_hz: float = 20.0,
    stop_hz: float = 20000.0,
    num_points: int = 1000,
    scale: str = "log",
) -> np.ndarray:
    """Generates standard frequency grid between start_hz and stop_hz.

    Args:
        start_hz: Minimum frequency (default 20.0 Hz).
        stop_hz: Maximum frequency (default 20000.0 Hz).
        num_points: Grid resolution (default 1000 points).
        scale: "log" (logarithmic spacing via np.geomspace) or "linear" (np.linspace).

    Returns:
        1D numpy array of frequencies in Hz.
    """
    if start_hz <= 0:
        raise ValueError(f"start_hz must be positive, got {start_hz}")
    if stop_hz <= start_hz:
        raise ValueError(f"stop_hz ({stop_hz}) must be greater than start_hz ({start_hz})")
    if num_points <= 1:
        raise ValueError(f"num_points must be > 1, got {num_points}")

    if scale.lower() == "log":
        return np.geomspace(start_hz, stop_hz, num_points)
    elif scale.lower() == "linear":
        return np.linspace(start_hz, stop_hz, num_points)
    else:
        raise ValueError(f"Unsupported scale '{scale}', expected 'log' or 'linear'")


def _extract_biquad_coeffs(biquad: Any) -> Tuple[float, float, float, float, float]:
    """Extracts (a1, a2, b0, b1, b2) in standard form."""
    if hasattr(biquad, "to_standard"):
        std = biquad.to_standard()
        return std.a1, std.a2, std.b0, std.b1, std.b2
    elif hasattr(biquad, "a1") and hasattr(biquad, "b0"):
        return biquad.a1, biquad.a2, biquad.b0, biquad.b1, biquad.b2
    elif isinstance(biquad, (list, tuple)) and len(biquad) == 5:
        return float(biquad[0]), float(biquad[1]), float(biquad[2]), float(biquad[3]), float(biquad[4])
    raise TypeError(f"Cannot extract biquad coefficients from {type(biquad)}")


def evaluate_biquad_dtft(
    biquad: Any,
    frequencies: np.ndarray,
    sample_rate: int = 48000,
) -> FrequencyResponse:
    """Evaluates DTFT H(e^jw) for a single second-order biquad section.

    Vectorized DTFT computation using:
        w = 2 * pi * f / sample_rate
        z^-1 = exp(-j * w)
        z^-2 = exp(-2j * w)
        H = (b0 + b1*z^-1 + b2*z^-2) / (1.0 + a1*z^-1 + a2*z^-2)

    Returns:
        FrequencyResponse dataclass.
    """
    a1, a2, b0, b1, b2 = _extract_biquad_coeffs(biquad)
    w = 2.0 * np.pi * frequencies / float(sample_rate)
    ejw = np.exp(-1j * w)
    ej2w = np.exp(-2j * w)

    num = b0 + b1 * ejw + b2 * ej2w
    den = 1.0 + a1 * ejw + a2 * ej2w

    # Avoid zero division
    den_safe = np.where(np.abs(den) < 1e-15, 1e-15 + 0j, den)
    h = num / den_safe

    mag_lin = np.abs(h)
    mag_db = 20.0 * np.log10(np.maximum(mag_lin, 1e-15))
    phase_rad = np.unwrap(np.angle(h))

    return FrequencyResponse(
        frequencies=frequencies,
        complex_response=h,
        magnitude_linear=mag_lin,
        magnitude_db=mag_db,
        phase_rad=phase_rad,
        sample_rate=sample_rate,
    )


def evaluate_cascade_dtft(
    biquads: Sequence[Any],
    frequencies: np.ndarray,
    sample_rate: int = 48000,
    gain_db: float = 0.0,
) -> FrequencyResponse:
    """Evaluates cumulative DTFT response for a series cascade of biquads.

    Cumulative complex response is product of individual stage responses,
    scaled by block trim gain. Cumulative dB magnitude is sum of stage gains + gain_db.

    Returns:
        FrequencyResponse dataclass.
    """
    if not biquads:
        raise ValueError("Cannot evaluate empty biquad sequence")

    w = 2.0 * np.pi * frequencies / float(sample_rate)
    ejw = np.exp(-1j * w)
    ej2w = np.exp(-2j * w)

    h_tot = np.ones(len(frequencies), dtype=np.complex128)
    for b in biquads:
        a1, a2, b0, b1, b2 = _extract_biquad_coeffs(b)
        num = b0 + b1 * ejw + b2 * ej2w
        den = 1.0 + a1 * ejw + a2 * ej2w
        den_safe = np.where(np.abs(den) < 1e-15, 1e-15 + 0j, den)
        h_tot *= (num / den_safe)

    if gain_db != 0.0:
        h_tot *= (10.0 ** (gain_db / 20.0))

    mag_lin = np.abs(h_tot)
    mag_db = 20.0 * np.log10(np.maximum(mag_lin, 1e-15))
    phase_rad = np.unwrap(np.angle(h_tot))

    return FrequencyResponse(
        frequencies=frequencies,
        complex_response=h_tot,
        magnitude_linear=mag_lin,
        magnitude_db=mag_db,
        phase_rad=phase_rad,
        sample_rate=sample_rate,
    )


def evaluate_alignment_block_dtft(
    block: Any,
    frequencies: Optional[np.ndarray] = None,
    sample_rate: int = 48000,
) -> FrequencyResponse:
    """Evaluates full frequency response for a Sony CXD90050 alignment block.

    Simulates the 5 internal cascaded biquads and block gain_db. If frequencies
    is None, defaults to 20 Hz - 20 kHz logarithmic grid (1000 points).
    """
    if frequencies is None:
        nyquist = sample_rate / 2.0
        max_f = min(20000.0, nyquist - 10.0)
        frequencies = create_frequency_grid(start_hz=20.0, stop_hz=max_f, num_points=1000, scale="log")

    gain_db = getattr(block, "gain_db", 0.0)
    biquads = getattr(block, "biquads", block)
    return evaluate_cascade_dtft(biquads, frequencies, sample_rate=sample_rate, gain_db=gain_db)


def verify_attenuation_band(
    response: FrequencyResponse,
    band: Tuple[float, float] = (50.0, 800.0),
    min_gain_db: float = 6.0,
    max_gain_db: float = 9.0,
) -> BandVerificationResult:
    """Inspects response across target band and asserts minimum and maximum gain.

    Extracts frequencies in [band[0], band[1]]. Computes min, max, mean gain.
    Asserts min_gain >= min_gain_db and max_gain <= max_gain_db.

    Returns:
        BandVerificationResult with passed flag and metrics.
    """
    f_low, f_high = band
    mask = (response.frequencies >= f_low) & (response.frequencies <= f_high)
    in_band = response.magnitude_db[mask]

    if len(in_band) == 0:
        # Fall back to interpolating 50 points across band
        band_eval_f = np.linspace(f_low, f_high, 50)
        in_band = np.interp(band_eval_f, response.frequencies, response.magnitude_db)

    min_g = float(np.min(in_band))
    max_g = float(np.max(in_band))
    mean_g = float(np.mean(in_band))

    passed = (min_g >= min_gain_db - 1e-4) and (max_g <= max_gain_db + 1e-4)
    reason = None
    if not passed:
        reasons = []
        if min_g < min_gain_db - 1e-4:
            reasons.append(f"Min gain {min_g:.2f} dB < required {min_gain_db:.2f} dB")
        if max_g > max_gain_db + 1e-4:
            reasons.append(f"Max gain {max_g:.2f} dB > allowed {max_gain_db:.2f} dB")
        reason = "; ".join(reasons)

    return BandVerificationResult(
        passed=passed,
        band=band,
        min_gain_db=min_g,
        max_gain_db=max_g,
        mean_gain_db=mean_g,
        required_min_db=min_gain_db,
        required_max_db=max_gain_db,
        failure_reason=reason,
    )


def assert_attenuation_band(
    response: FrequencyResponse,
    band: Tuple[float, float] = (50.0, 800.0),
    min_gain_db: float = 6.0,
    max_gain_db: float = 9.0,
) -> BandVerificationResult:
    """Asserts attenuation band compliance and raises AttenuationBandViolationError if failed.

    Returns:
        BandVerificationResult if compliant.

    Raises:
        AttenuationBandViolationError: If min gain < 6.0 dB or max gain > 9.0 dB.
    """
    res = verify_attenuation_band(response, band=band, min_gain_db=min_gain_db, max_gain_db=max_gain_db)
    if not res.passed:
        raise AttenuationBandViolationError(
            f"Attenuation band {band[0]:.0f}-{band[1]:.0f} Hz assertion failed: {res.failure_reason}",
            band=band,
            min_gain_db=res.min_gain_db,
            max_gain_db=res.max_gain_db,
            required_min_db=min_gain_db,
            required_max_db=max_gain_db,
        )
    return res


def render_ascii_response_curve(
    frequencies: np.ndarray,
    magnitude_db: np.ndarray,
    width: int = 60,
    height: int = 12,
    band_highlight: Optional[Tuple[float, float]] = (50.0, 800.0),
) -> str:
    """Renders a 2D ASCII / Unicode plot of the frequency response for CLI dry-run.

    Plots dB gain on vertical axis and frequency on horizontal axis with border boxes,
    tick labels, and target band indicators. Suitable for rich/click terminal display.
    """
    if len(frequencies) == 0 or len(magnitude_db) == 0:
        return "[Empty Frequency Response]"

    min_db = float(np.min(magnitude_db))
    max_db = float(np.max(magnitude_db))

    # Pad vertical range slightly so curves aren't glued to borders
    if abs(max_db - min_db) < 0.1:
        v_min = min_db - 1.0
        v_max = max_db + 1.0
    else:
        span = max_db - min_db
        v_min = min_db - 0.05 * span
        v_max = max_db + 0.05 * span

    # Sample to width columns
    log_f = np.log10(np.maximum(frequencies, 1.0))
    f_min_log = float(log_f[0])
    f_max_log = float(log_f[-1])

    grid = [[" " for _ in range(width)] for _ in range(height)]

    # Interpolate for each column
    col_f_logs = np.linspace(f_min_log, f_max_log, width)
    col_dbs = np.interp(col_f_logs, log_f, magnitude_db)

    # Plot points
    for col in range(width):
        db_val = col_dbs[col]
        # Normalize to row index (row 0 is v_max, row height-1 is v_min)
        norm_y = (db_val - v_min) / (v_max - v_min)
        norm_y = max(0.0, min(1.0, norm_y))
        row = int(round((1.0 - norm_y) * (height - 1)))
        row = max(0, min(height - 1, row))

        # Check if in highlighted band
        freq_at_col = 10.0 ** col_f_logs[col]
        is_in_band = False
        if band_highlight:
            is_in_band = (band_highlight[0] <= freq_at_col <= band_highlight[1])

        char = "■" if is_in_band else "*"
        grid[row][col] = char

    # Build ASCII output lines with borders and Y labels
    lines: List[str] = []
    header = f"  Frequency Response Curve (Magnitude dB vs Frequency)"
    lines.append(header)
    lines.append(f"       ┌{'─' * width}┐")

    for r in range(height):
        # Calculate Y value for this row
        row_db = v_max - (r / (height - 1)) * (v_max - v_min)
        y_label = f"{row_db:+5.1f}dB│"
        row_content = "".join(grid[r])
        lines.append(f"{y_label}{row_content}│")

    lines.append(f"       └{'─' * width}┘")

    # X axis label
    f_start = frequencies[0]
    f_end = frequencies[-1]
    axis_label = f"        {f_start:.0f}Hz" + " " * (width - 16) + f"{f_end:.0f}Hz"
    lines.append(axis_label)
    if band_highlight:
        lines.append(f"        [■ = Target Band {band_highlight[0]:.0f}Hz - {band_highlight[1]:.0f}Hz]")

    return "\n".join(lines)
