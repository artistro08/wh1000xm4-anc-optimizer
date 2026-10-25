"""Discrete-time Nyquist and Schur-Cohn stability verification engine.

Provides:
- Characteristic root computation for 2nd-order IIR biquad sections
- Algebraic Schur-Cohn / Jury stability validation
- Geometric pole-radius and parameter-boundary stability margins
- Fixed-point (Q28) quantization invariance proofs
- Exception and report data models for stability validation
"""

from __future__ import annotations

import cmath
from dataclasses import dataclass
import math
from typing import Any, List, Optional, Sequence, Tuple, Union

import numpy as np

from xm4_optimizer.dsp.fixed_point import float_to_q28, q28_to_float


class FilterInstabilityError(ValueError):
    """Raised when an IIR biquad filter fails Nyquist / Schur-Cohn stability verification."""

    def __init__(
        self,
        message: str,
        poles: Optional[Tuple[complex, complex]] = None,
        max_pole_radius: Optional[float] = None,
        a1: Optional[float] = None,
        a2: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.poles = poles
        self.max_pole_radius = max_pole_radius
        self.a1 = a1
        self.a2 = a2


@dataclass(frozen=True)
class PolePair:
    """Pair of discrete-time characteristic roots (poles)."""

    p1: complex
    p2: complex

    @property
    def max_radius(self) -> float:
        """Returns the maximum magnitude of the pole pair."""
        return max(abs(self.p1), abs(self.p2))

    @property
    def is_complex_conjugate(self) -> bool:
        """Returns True if poles are a complex conjugate pair."""
        return abs(self.p1.real - self.p2.real) < 1e-12 and abs(
            self.p1.imag + self.p2.imag
        ) < 1e-12

    def as_tuple(self) -> Tuple[complex, complex]:
        return (self.p1, self.p2)


@dataclass(frozen=True)
class StabilityMargin:
    """Quantitative stability margins."""

    pole_margin: float  # delta_pole = 1.0 - max(|p|)
    param_margin: float  # delta_param = min(1 - a2, 1 + a2 - |a1|)
    max_pole_radius: float
    is_strictly_stable: bool  # True if pole_margin > 0 and param_margin > 0


@dataclass(frozen=True)
class QuantizationStabilityReport:
    """Verification report for fixed-point quantization invariance."""

    original_poles: PolePair
    quantized_poles: PolePair
    original_margin: float
    quantized_margin: float
    pole_shift: float  # max(|p_quant - p_orig|)
    safety_headroom_ratio: float  # param_margin / (2^-format_bits)
    is_invariant: bool  # True if quantized filter remains strictly stable


@dataclass(frozen=True)
class StabilityReport:
    """Comprehensive stability evaluation report for a biquad."""

    is_stable: bool
    poles: PolePair
    margin: StabilityMargin
    schur_cohn_passed: bool
    quantization_report: Optional[QuantizationStabilityReport] = None
    failure_reason: Optional[str] = None


def compute_characteristic_roots(
    a1: float, a2: float, a0: float = 1.0
) -> PolePair:
    """Computes discrete-time characteristic roots p1, p2 of a0*z^2 + a1*z + a2 = 0.

    Uses closed-form analytical quadratic formula with cmath for zero-overhead precision.

    Args:
        a1: Coefficient of z^-1 (or z in characteristic equation).
        a2: Coefficient of z^-2 (or constant term in characteristic equation).
        a0: Coefficient of z^0 (default 1.0). Must be non-zero.

    Returns:
        PolePair containing (p1, p2).

    Raises:
        ValueError: If a0 == 0.0 (degenerate first-order system).
    """
    if a0 == 0.0:
        raise ValueError("Denominator coefficient a0 must be non-zero")

    norm_a1 = a1 / a0
    norm_a2 = a2 / a0
    disc = norm_a1 * norm_a1 - 4.0 * norm_a2
    sqrt_disc = cmath.sqrt(disc)
    p1 = (-norm_a1 + sqrt_disc) / 2.0
    p2 = (-norm_a1 - sqrt_disc) / 2.0
    return PolePair(p1=p1, p2=p2)


def check_schur_cohn(
    a1: float, a2: float, a0: float = 1.0, tolerance: float = 1e-12
) -> bool:
    """Evaluates algebraic Schur-Cohn / Jury conditions for 2nd order polynomial.

    Conditions (normalized by a0):
        1. |a2 / a0| < 1.0 - tolerance
        2. |a1 / a0| < 1.0 + (a2 / a0) - tolerance

    Args:
        a1: Denominator coefficient a1.
        a2: Denominator coefficient a2.
        a0: Denominator coefficient a0 (default 1.0).
        tolerance: Safety epsilon strictly inside unit circle.

    Returns:
        True if all conditions are satisfied; False otherwise.
    """
    if a0 == 0.0:
        raise ValueError("Denominator coefficient a0 must be non-zero")

    norm_a1 = a1 / a0
    norm_a2 = a2 / a0

    cond1 = abs(norm_a2) < (1.0 - tolerance)
    cond2 = abs(norm_a1) < (1.0 + norm_a2 - tolerance)
    return cond1 and cond2


def compute_stability_margins(
    a1: float, a2: float, a0: float = 1.0
) -> StabilityMargin:
    """Computes pole-radius margin and parameter space boundary margin.

    delta_pole = 1.0 - max(|p1|, |p2|)
    delta_param = min(1.0 - a2/a0, 1.0 + a2/a0 - |a1/a0|)

    Args:
        a1: Denominator coefficient a1.
        a2: Denominator coefficient a2.
        a0: Denominator coefficient a0.

    Returns:
        StabilityMargin dataclass with margins and strict stability flag.
    """
    roots = compute_characteristic_roots(a1, a2, a0)
    max_r = roots.max_radius
    pole_margin = 1.0 - max_r

    norm_a1 = a1 / a0
    norm_a2 = a2 / a0
    cond1 = 1.0 - abs(norm_a2)
    cond2 = 1.0 + norm_a2 - abs(norm_a1)
    param_margin = min(cond1, cond2)
    is_strictly_stable = (pole_margin > 0.0) and (param_margin > 0.0)

    return StabilityMargin(
        pole_margin=pole_margin,
        param_margin=param_margin,
        max_pole_radius=max_r,
        is_strictly_stable=is_strictly_stable,
    )


def _extract_a1_a2_a0(
    biquad_or_a1: Any, a2: Optional[float] = None, a0: float = 1.0
) -> Tuple[float, float, float]:
    """Helper to extract a1, a2, a0 from either a BiquadCoefficients or direct floats."""
    if hasattr(biquad_or_a1, "to_standard"):
        std = biquad_or_a1.to_standard()
        return std.a1, std.a2, 1.0
    elif hasattr(biquad_or_a1, "a1") and hasattr(biquad_or_a1, "a2"):
        return biquad_or_a1.a1, biquad_or_a1.a2, 1.0
    else:
        if a2 is None:
            raise ValueError("When passing a1 as float, a2 must also be provided")
        return float(biquad_or_a1), float(a2), a0


def is_biquad_stable(
    biquad_or_a1: Any,
    a2: Optional[float] = None,
    a0: float = 1.0,
    tolerance: float = 1e-12,
) -> bool:
    """Enforces np.all(np.abs(poles) < 1.0 - tolerance).

    Accepts either:
        - BiquadCoefficients instance: is_biquad_stable(coefs)
        - Denominator floats: is_biquad_stable(a1, a2)

    Returns:
        True if strictly stable, False otherwise.
    """
    a1_val, a2_val, a0_val = _extract_a1_a2_a0(biquad_or_a1, a2, a0)
    roots = compute_characteristic_roots(a1_val, a2_val, a0_val)
    poles = np.array([roots.p1, roots.p2])
    return bool(np.all(np.abs(poles) < (1.0 - tolerance)))


def assert_biquad_stable(
    biquad_or_a1: Any,
    a2: Optional[float] = None,
    a0: float = 1.0,
    tolerance: float = 1e-12,
    filter_name: str = "Biquad",
) -> StabilityReport:
    """Verifies stability and raises FilterInstabilityError if unstable.

    Returns:
        StabilityReport if stable.

    Raises:
        FilterInstabilityError: If max(|p|) >= 1.0 - tolerance.
    """
    a1_val, a2_val, a0_val = _extract_a1_a2_a0(biquad_or_a1, a2, a0)
    roots = compute_characteristic_roots(a1_val, a2_val, a0_val)
    margin = compute_stability_margins(a1_val, a2_val, a0_val)
    schur_passed = check_schur_cohn(a1_val, a2_val, a0_val, tolerance=tolerance)
    stable = (roots.max_radius < (1.0 - tolerance)) and schur_passed

    if not stable:
        raise FilterInstabilityError(
            f"{filter_name} filter is unstable: max pole radius {roots.max_radius:.6f} >= {1.0 - tolerance:.6f} "
            f"(a1={a1_val:.6f}, a2={a2_val:.6f})",
            poles=roots.as_tuple(),
            max_pole_radius=roots.max_radius,
            a1=a1_val,
            a2=a2_val,
        )

    return StabilityReport(
        is_stable=True,
        poles=roots,
        margin=margin,
        schur_cohn_passed=schur_passed,
    )


def compute_biquad_poles(a1: float, a2: float, a0: float = 1.0) -> np.ndarray:
    """Returns a complex numpy array [p1, p2] of the biquad's discrete-time poles."""
    roots = compute_characteristic_roots(a1, a2, a0=a0)
    return np.array([roots.p1, roots.p2], dtype=np.complex128)


def verify_nyquist_stability(biquad_or_a1: Any, a2: Optional[float] = None) -> bool:
    """Verifies that all poles lie strictly inside the unit circle (|p| < 1.0)."""
    return is_biquad_stable(biquad_or_a1, a2=a2, tolerance=0.0)


def verify_quantization_invariance(
    a1: float,
    a2: float,
    format_bits: int = 28,  # Q28 default
    a0: float = 1.0,
) -> QuantizationStabilityReport:
    """Mathematically verifies that fixed-point quantization preserves stability.

    Quantizes a1, a2 using Q28 (or specified bits), computes quantized roots,
    and proves that max(|p_quant|) < 1.0 with positive stability margin.

    Returns:
        QuantizationStabilityReport detailing original vs quantized poles,
        shift distance, and safety headroom ratio.
    """
    orig_roots = compute_characteristic_roots(a1, a2, a0)
    orig_margin = compute_stability_margins(a1, a2, a0)

    # Quantize
    scale = float(1 << format_bits)
    q_a1 = round((a1 / a0) * scale) / scale
    q_a2 = round((a2 / a0) * scale) / scale

    q_roots = compute_characteristic_roots(q_a1, q_a2, 1.0)
    q_margin = compute_stability_margins(q_a1, q_a2, 1.0)

    # Compute minimum pole shift under pairing
    shift1 = max(abs(q_roots.p1 - orig_roots.p1), abs(q_roots.p2 - orig_roots.p2))
    shift2 = max(abs(q_roots.p1 - orig_roots.p2), abs(q_roots.p2 - orig_roots.p1))
    pole_shift = min(shift1, shift2)

    lsb = 1.0 / scale
    safety_ratio = orig_margin.param_margin / lsb if lsb > 0 else 0.0
    is_invariant = q_margin.is_strictly_stable

    return QuantizationStabilityReport(
        original_poles=orig_roots,
        quantized_poles=q_roots,
        original_margin=orig_margin.pole_margin,
        quantized_margin=q_margin.pole_margin,
        pole_shift=pole_shift,
        safety_headroom_ratio=safety_ratio,
        is_invariant=is_invariant,
    )


def validate_alignment_block_stability(
    block: Any,  # CXD90050AlignmentBlock
    tolerance: float = 1e-12,
) -> List[StabilityReport]:
    """Validates stability across all cascaded biquads in a CXD90050 alignment block.

    Validates all 5 biquad sections (e.g. ALIGN_FF_L). Raises FilterInstabilityError
    if any section is unstable.
    """
    reports: List[StabilityReport] = []
    for idx, biquad in enumerate(block.biquads):
        rep = assert_biquad_stable(
            biquad, tolerance=tolerance, filter_name=f"Block_{block.filter_id}_Biquad_{idx}"
        )
        reports.append(rep)
    return reports
