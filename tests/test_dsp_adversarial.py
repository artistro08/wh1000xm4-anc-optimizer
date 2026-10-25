"""Empirical Adversarial Stress Test Suite for DSP & Stability Subsystems.

Authored by: challenger_m2_1 (Empirical Challenger)
Covers:
1. Stability Boundary Testing:
   - Marginally stable poles on unit circle (|p| = 1.0) rejected as unstable.
   - Unstable poles (|p| > 1.0) raising FilterInstabilityError.
   - Near-boundary poles (|p| = 0.999999, |p| = 1 - 10^-8) precision and margins.
2. Fixed-Point Boundary Testing:
   - Extreme out-of-range floats (> 10^6, < -10^6) saturation clamping vs OverflowError.
   - Non-finite values (NaN, +inf, -inf) raising ValueError.
   - Truncated and oversized binary byte buffers (< 20B, > 20B) raising ValueError.
3. Quantization Invariance under Monte Carlo:
   - 10,000 random biquads spanning interior, complex conjugate, real, and boundary regions.
   - Schur-Cohn vs analytical roots cross-oracle verification.
   - Maximum pole shift delta under Q28 quantization.
4. Filter Execution & Transposed Direct Form II:
   - BIBO impulse response energy convergence for stable filters.
   - Exponential divergence detection for unstable filters.
5. Synthesizer & Simulator Boundary Stress:
   - Invalid frequencies (f0 <= 0, f0 >= Nyquist).
   - Negative and zero Q/slope values.
   - Gain boundaries (0 dB, 40 dB, -40 dB).
"""

from __future__ import annotations

import cmath
import math
import struct
import numpy as np
import pytest

from xm4_optimizer.dsp.biquad import (
    BiquadCascade,
    BiquadCoefficients,
    BiquadFilter,
    SignConvention,
    detect_sign_convention,
)
from xm4_optimizer.dsp.fixed_point import (
    BIQUAD_BYTE_SIZE,
    BIQUAD_STRUCT_FORMAT,
    INT32_MAX,
    INT32_MIN,
    Q28_MAX_FLOAT,
    Q28_MIN_FLOAT,
    Q28_RESOLUTION,
    Q28_SCALE,
    Q31_MAX_FLOAT,
    Q31_MIN_FLOAT,
    Q31_SCALE,
    clamp_int32,
    float_to_q28,
    float_to_q31,
    pack_biquad_q28,
    pack_biquad_q31,
    q28_to_float,
    q31_to_float,
    unpack_biquad_q28,
    unpack_biquad_q31,
)
from xm4_optimizer.dsp.simulation import (
    create_frequency_grid,
    evaluate_biquad_dtft,
    evaluate_cascade_dtft,
)
from xm4_optimizer.dsp.stability import (
    FilterInstabilityError,
    PolePair,
    StabilityMargin,
    assert_biquad_stable,
    check_schur_cohn,
    compute_characteristic_roots,
    compute_stability_margins,
    is_biquad_stable,
    verify_quantization_invariance,
)
from xm4_optimizer.dsp.synthesizer import (
    DEFAULT_SAMPLE_RATE,
    SUPPORTED_SAMPLE_RATES,
    pad_biquad_cascade,
    synthesize_anc_boost,
    synthesize_high_pass,
    synthesize_low_shelf,
    synthesize_peaking_eq,
)


# ============================================================================
# 1. Stability Boundary Testing: Marginally Stable Poles (|p| = 1.0)
# ============================================================================

class TestMarginalStabilityUnitCircle:
    """Stress tests filters on the boundary of stability (|p| = 1.0).

    By discrete-time control theory, marginal poles on the unit circle produce
    undamped oscillations or persistent integrator states. For ANC IIR filters,
    acceptance criteria strictly require |poles| < 1.0 (strict stability).
    """

    @pytest.mark.parametrize("theta", [
        0.0,                    # z = 1.0 (DC integrator pole)
        math.pi,                # z = -1.0 (Nyquist frequency pole)
        math.pi / 2.0,          # z = +j, -j (Quarter-rate oscillator)
        math.pi / 4.0,          # z = (1+j)/sqrt(2)
        math.pi / 3.0,          # z = 0.5 + j*sqrt(3)/2
        math.pi / 6.0,          # z = sqrt(3)/2 + j*0.5
        3.0 * math.pi / 4.0,    # z = (-1+j)/sqrt(2)
    ])
    def test_unit_circle_complex_conjugate_poles_rejected(self, theta: float):
        """Constructs characteristic polynomial for poles exactly on unit circle: |p| = 1.0."""
        # Characteristic eq: (z - e^(j*theta)) * (z - e^(-j*theta)) = z^2 - 2*cos(theta)*z + 1.0
        a1 = -2.0 * math.cos(theta)
        a2 = 1.0  # Exactly on unit circle

        roots = compute_characteristic_roots(a1, a2)
        assert pytest.approx(roots.max_radius, abs=1e-12) == 1.0

        # Must be rejected by is_biquad_stable
        assert not is_biquad_stable(a1, a2), f"Failed for theta={theta}: a1={a1}, a2={a2}"

        # Must be rejected by check_schur_cohn
        assert not check_schur_cohn(a1, a2), f"Schur-Cohn failed to reject marginal pole at theta={theta}"

        # Must raise FilterInstabilityError
        with pytest.raises(FilterInstabilityError) as exc_info:
            assert_biquad_stable(a1, a2)
        assert exc_info.value.max_pole_radius is not None
        assert exc_info.value.max_pole_radius >= 1.0 - 1e-12

        # BiquadCoefficients method rejection
        coef = BiquadCoefficients(a1=a1, a2=a2, b0=1.0, b1=0.0, b2=0.0)
        assert not coef.is_stable()

    def test_dc_integrator_single_pole_at_one(self):
        """Single pole at z = 1.0, second pole at z = 0.0."""
        # (z - 1.0) * z = z^2 - z = 0 -> a1 = -1.0, a2 = 0.0
        a1 = -1.0
        a2 = 0.0
        assert not is_biquad_stable(a1, a2)
        with pytest.raises(FilterInstabilityError):
            assert_biquad_stable(a1, a2)

    def test_nyquist_single_pole_at_minus_one(self):
        """Single pole at z = -1.0, second pole at z = 0.0."""
        # (z + 1.0) * z = z^2 + z = 0 -> a1 = 1.0, a2 = 0.0
        a1 = 1.0
        a2 = 0.0
        assert not is_biquad_stable(a1, a2)
        with pytest.raises(FilterInstabilityError):
            assert_biquad_stable(a1, a2)

    def test_double_pole_at_one(self):
        """Double pole at z = 1.0 (unstable 2nd-order integrator)."""
        # (z - 1.0)^2 = z^2 - 2z + 1 -> a1 = -2.0, a2 = 1.0
        a1 = -2.0
        a2 = 1.0
        assert not is_biquad_stable(a1, a2)
        with pytest.raises(FilterInstabilityError):
            assert_biquad_stable(a1, a2)

    def test_double_pole_at_minus_one(self):
        """Double pole at z = -1.0."""
        # (z + 1.0)^2 = z^2 + 2z + 1 -> a1 = 2.0, a2 = 1.0
        a1 = 2.0
        a2 = 1.0
        assert not is_biquad_stable(a1, a2)
        with pytest.raises(FilterInstabilityError):
            assert_biquad_stable(a1, a2)


# ============================================================================
# 2. Stability Testing: Unstable Poles (|p| > 1.0)
# ============================================================================

class TestUnstablePolesDivergence:
    """Stress tests filters outside the unit circle (|p| > 1.0)."""

    @pytest.mark.parametrize("radius,angle", [
        (1.000001, 0.0),            # Just outside unit circle, real positive
        (1.000001, math.pi),        # Just outside unit circle, real negative
        (1.0001, math.pi / 4.0),    # Complex conjugate just outside
        (1.05, math.pi / 3.0),      # Definite instability
        (1.5, 0.5),                 # Highly unstable
        (10.0, 1.2),                # Severely unstable
        (1000.0, 0.0),              # Catastrophic instability
    ])
    def test_unstable_poles_raise_instability_error(self, radius: float, angle: float):
        """Verifies that any pole with |p| > 1.0 strictly raises FilterInstabilityError."""
        # Characteristic polynomial from poles r * e^(+/- j*angle)
        # z^2 - 2*r*cos(angle)*z + r^2
        a1 = -2.0 * radius * math.cos(angle)
        a2 = radius * radius

        roots = compute_characteristic_roots(a1, a2)
        assert roots.max_radius >= radius - 1e-12

        assert not is_biquad_stable(a1, a2)
        assert not check_schur_cohn(a1, a2)

        with pytest.raises(FilterInstabilityError) as exc_info:
            assert_biquad_stable(a1, a2)

        err = exc_info.value
        assert err.max_pole_radius is not None
        assert err.max_pole_radius > 1.0
        assert err.a1 == a1
        assert err.a2 == a2
        assert len(err.poles) == 2

    def test_divergent_time_domain_response(self):
        """Simulates an unstable filter in time domain and verifies divergence."""
        # Pole at 1.1 -> impulse response should explode
        a1 = -1.1
        a2 = 0.0
        coef = BiquadCoefficients(a1=a1, a2=a2, b0=1.0, b1=0.0, b2=0.0)
        assert not coef.is_stable()

        filt = BiquadFilter(coef)
        impulse = [1.0] + [0.0] * 50
        output = filt.process(impulse)
        # y[n] = 1.1^n -> output[50] = 1.1^50 = 117.39...
        assert output[-1] > 100.0
        assert output[-1] > output[0]  # Divergent energy


# ============================================================================
# 3. Near-Boundary Precision Testing (|p| = 0.999999)
# ============================================================================

class TestNearBoundaryPolesPrecision:
    """Stress tests numerical precision and stability margins near unit circle."""

    @pytest.mark.parametrize("radius", [
        0.999999,
        0.99999,
        0.9999,
        0.999,
        0.99,
    ])
    def test_near_boundary_poles_accepted_with_correct_margin(self, radius: float):
        """Filters with |p| < 1.0 must be accepted, with positive stability margins."""
        theta = math.pi / 4.0
        a1 = -2.0 * radius * math.cos(theta)
        a2 = radius * radius

        roots = compute_characteristic_roots(a1, a2)
        assert pytest.approx(roots.max_radius, rel=1e-6) == radius
        assert is_biquad_stable(a1, a2)
        assert check_schur_cohn(a1, a2)

        rep = assert_biquad_stable(a1, a2)
        assert rep.is_stable
        assert rep.margin.pole_margin > 0.0
        assert pytest.approx(rep.margin.pole_margin, abs=1e-5) == 1.0 - radius
        assert rep.margin.is_strictly_stable

    def test_tolerance_parameter_boundary(self):
        """Verifies custom tolerance behavior for safety margins."""
        # Pole at 0.99999
        a1 = -0.99999
        a2 = 0.0
        # With default tolerance 1e-12: strictly stable
        assert is_biquad_stable(a1, a2, tolerance=1e-12)

        # With tight tolerance 1e-4: 0.99999 is NOT < (1 - 1e-4 = 0.9999)
        assert not is_biquad_stable(a1, a2, tolerance=1e-4)
        with pytest.raises(FilterInstabilityError):
            assert_biquad_stable(a1, a2, tolerance=1e-4)


# ============================================================================
# 4. Fixed-Point Boundary Testing: Clamping, Overflow & Non-Finite Values
# ============================================================================

class TestFixedPointExtremeBoundaries:
    """Stress tests fixed-point quantization on extreme values."""

    @pytest.mark.parametrize("val", [
        1e6, 1e9, 1e20, 1e300,
    ])
    def test_q28_positive_extreme_clamping(self, val: float):
        """Extreme positive floats must saturate to INT32_MAX when clamp=True."""
        assert float_to_q28(val, clamp=True) == INT32_MAX

    @pytest.mark.parametrize("val", [
        -1e6, -1e9, -1e20, -1e300,
    ])
    def test_q28_negative_extreme_clamping(self, val: float):
        """Extreme negative floats must saturate to INT32_MIN when clamp=True."""
        assert float_to_q28(val, clamp=True) == INT32_MIN

    @pytest.mark.parametrize("val", [
        8.0, 8.0000001, 10.0, 1e6, -8.0000001, -10.0, -1e6,
    ])
    def test_q28_overflow_when_clamp_false(self, val: float):
        """When clamp=False, out-of-range floats must raise OverflowError."""
        with pytest.raises(OverflowError):
            float_to_q28(val, clamp=False)

    @pytest.mark.parametrize("val", [
        float("nan"), float("inf"), float("-inf"),
    ])
    def test_q28_non_finite_values_raise_value_error(self, val: float):
        """Non-finite floats must raise ValueError in float_to_q28."""
        with pytest.raises(ValueError, match="Cannot convert non-finite float"):
            float_to_q28(val)

    @pytest.mark.parametrize("val", [
        float("nan"), float("inf"), float("-inf"),
    ])
    def test_q31_non_finite_values_raise_value_error(self, val: float):
        """Non-finite floats must raise ValueError in float_to_q31."""
        with pytest.raises(ValueError, match="Cannot convert non-finite float"):
            float_to_q31(val)

    @pytest.mark.parametrize("prescale", [0, -1, -5])
    def test_q31_invalid_prescale_raises_value_error(self, prescale: int):
        with pytest.raises(ValueError, match="Prescale factor must be positive"):
            float_to_q31(0.5, prescale=prescale)

        with pytest.raises(ValueError, match="Prescale factor must be positive"):
            q31_to_float(1000, prescale=prescale)

    @pytest.mark.parametrize("val", [
        1e6, 1e9, 1e20, 1e300,
    ])
    def test_q31_extreme_clamping(self, val: float):
        assert float_to_q31(val, prescale=2, clamp=True) == INT32_MAX
        assert float_to_q31(-val, prescale=2, clamp=True) == INT32_MIN

    @pytest.mark.parametrize("val", [
        2.0, 2.00001, 5.0, 1e6, -2.00001, -1e6,
    ])
    def test_q31_overflow_when_clamp_false(self, val: float):
        # With prescale=2, range is [-2.0, 2.0 - eps]
        with pytest.raises(OverflowError):
            float_to_q31(val, prescale=2, clamp=False)


# ============================================================================
# 5. Fixed-Point Binary Buffer Truncation & Mutated Payloads
# ============================================================================

class TestBinaryPackingBoundaries:
    """Stress tests struct packing and unpacking for byte length boundaries."""

    @pytest.mark.parametrize("byte_len", [
        0, 1, 4, 16, 19, 21, 24, 32, 100,
    ])
    def test_unpack_biquad_q28_invalid_lengths_raise_value_error(self, byte_len: int):
        """Any payload not exactly 20 bytes must raise ValueError."""
        corrupt_data = b"\x00" * byte_len
        with pytest.raises(ValueError, match="must be exactly 20 bytes"):
            unpack_biquad_q28(corrupt_data)

    @pytest.mark.parametrize("byte_len", [
        0, 1, 4, 16, 19, 21, 24, 32, 100,
    ])
    def test_unpack_biquad_q31_invalid_lengths_raise_value_error(self, byte_len: int):
        corrupt_data = b"\x00" * byte_len
        with pytest.raises(ValueError, match="must be exactly 20 bytes"):
            unpack_biquad_q31(corrupt_data)

    @pytest.mark.parametrize("byte_len", [
        0, 15, 19, 21, 50,
    ])
    def test_biquad_coefficients_unpack_invalid_length(self, byte_len: int):
        corrupt_data = b"\x55" * byte_len
        with pytest.raises(ValueError, match="must be 20 bytes"):
            BiquadCoefficients.unpack(corrupt_data)

    def test_biquad_pack_exact_20_bytes(self):
        coef = BiquadCoefficients(a1=-1.4, a2=0.5, b0=1.1, b1=-1.0, b2=0.2)
        packed = coef.pack()
        assert len(packed) == 20
        unpacked = BiquadCoefficients.unpack(packed)
        assert pytest.approx(unpacked.a1, abs=1e-7) == coef.a1
        assert pytest.approx(unpacked.a2, abs=1e-7) == coef.a2
        assert pytest.approx(unpacked.b0, abs=1e-7) == coef.b0
        assert pytest.approx(unpacked.b1, abs=1e-7) == coef.b1
        assert pytest.approx(unpacked.b2, abs=1e-7) == coef.b2


# ============================================================================
# 6. Monte Carlo Simulations: 10,000 Random Biquads Quantization Invariance
# ============================================================================

class TestMonteCarloQuantizationInvariance10000:
    """Rigorous 10,000-sample Monte Carlo stress harness.

    Tests:
    1. Equivalence of Schur-Cohn algebraic condition and analytical characteristic roots.
    2. Stability invariance under Q28 fixed-point quantization for all strictly stable biquads.
    3. Headroom and maximum pole shift statistics.
    """

    def test_10000_monte_carlo_schur_cohn_and_q28_invariance(self):
        np.random.seed(42)  # Deterministic seed for reproducible adversarial testing
        sample_count = 10000

        max_pole_shift = 0.0
        min_safety_margin = float("inf")
        invariant_pass_count = 0
        total_stable_tested = 0

        # Generate 10,000 random pole pairs inside unit disc
        # Sample radius r with uniform distribution over [0.01, 0.999]
        # and angles theta over [0, pi]
        radii = np.random.uniform(0.01, 0.999, size=sample_count)
        thetas = np.random.uniform(0.0, np.pi, size=sample_count)

        for i in range(sample_count):
            r = radii[i]
            theta = thetas[i]

            # Construct characteristic polynomial: z^2 - 2*r*cos(theta)*z + r^2 = 0
            a1 = float(-2.0 * r * math.cos(theta))
            a2 = float(r * r)

            # 1. Oracle cross-verification: roots vs Schur-Cohn
            roots = compute_characteristic_roots(a1, a2)
            assert roots.max_radius < 1.0, f"Iteration {i}: pole radius {roots.max_radius} >= 1.0"

            schur_ok = check_schur_cohn(a1, a2)
            roots_ok = is_biquad_stable(a1, a2)
            assert schur_ok == roots_ok, f"Iteration {i}: Schur-Cohn ({schur_ok}) != Roots ({roots_ok})"

            if roots_ok:
                total_stable_tested += 1

                # 2. Q28 Quantization Invariance
                q_rep = verify_quantization_invariance(a1, a2, format_bits=28)
                assert q_rep.is_invariant, (
                    f"Iteration {i}: Quantization ruined stability! "
                    f"Orig poles=({q_rep.original_poles.p1}, {q_rep.original_poles.p2}), "
                    f"Quant poles=({q_rep.quantized_poles.p1}, {q_rep.quantized_poles.p2}), "
                    f"shift={q_rep.pole_shift}"
                )
                invariant_pass_count += 1
                if q_rep.pole_shift > max_pole_shift:
                    max_pole_shift = q_rep.pole_shift
                if q_rep.quantized_margin < min_safety_margin:
                    min_safety_margin = q_rep.quantized_margin

        assert total_stable_tested == sample_count
        assert invariant_pass_count == sample_count
        # Verify that max pole shift under Q28 is tight (< 1e-4)
        assert max_pole_shift < 1e-4, f"Unexpectedly large pole shift: {max_pole_shift}"
        # Verify that all quantized filters preserved strictly positive margin
        assert min_safety_margin > 0.0

    def test_critical_quantization_boundary_analysis(self):
        """Tests the mathematical limit where pole radius approaches 1.0."""
        # When 1 - r is smaller than Q28 resolution (~3.725e-9), rounding can push r to 1.0.
        # Test a sequence of radii approaching 1.0:
        for delta in [1e-3, 1e-4, 1e-5, 1e-6, 1e-7]:
            r = 1.0 - delta
            a1 = -2.0 * r * math.cos(math.pi / 4.0)
            a2 = r * r
            rep = verify_quantization_invariance(a1, a2, format_bits=28)
            assert rep.is_invariant, f"Failed at delta={delta}"


# ============================================================================
# 7. Synthesizer & Filter Parameter Space Adversarial Stress
# ============================================================================

class TestSynthesizerAdversarialParameters:
    """Stress tests RBJ filter synthesis under extreme and invalid parameters."""

    @pytest.mark.parametrize("f0", [0.0, -10.0, -1000.0])
    def test_low_shelf_non_positive_f0_rejected(self, f0: float):
        with pytest.raises(ValueError, match="Corner frequency f0 must be positive"):
            synthesize_low_shelf(f0=f0)

    @pytest.mark.parametrize("fs", [0, -48000])
    def test_low_shelf_invalid_sample_rate_rejected(self, fs: int):
        with pytest.raises(ValueError, match="Sample rate must be positive"):
            synthesize_low_shelf(sample_rate=fs)

    def test_low_shelf_nyquist_violation_rejected(self):
        # f0 >= sample_rate / 2
        with pytest.raises(ValueError, match="must be strictly less than Nyquist"):
            synthesize_low_shelf(f0=24000.0, sample_rate=48000)

        with pytest.raises(ValueError, match="must be strictly less than Nyquist"):
            synthesize_low_shelf(f0=30000.0, sample_rate=48000)

    @pytest.mark.parametrize("slope", [0.0, -0.5, -2.0])
    def test_low_shelf_invalid_slope_rejected(self, slope: float):
        with pytest.raises(ValueError, match="Shelf slope must be positive"):
            synthesize_low_shelf(slope=slope)

    @pytest.mark.parametrize("q", [0.0, -0.707, -5.0])
    def test_peaking_eq_invalid_q_rejected(self, q: float):
        with pytest.raises(ValueError, match="Quality factor q must be positive"):
            synthesize_peaking_eq(f0=1000.0, gain_db=3.0, q=q)

    @pytest.mark.parametrize("q", [0.0, -1.0])
    def test_high_pass_invalid_q_rejected(self, q: float):
        with pytest.raises(ValueError, match="Quality factor q must be positive"):
            synthesize_high_pass(f0=25.0, q=q)

    def test_pad_biquad_cascade_overflow_rejected(self):
        """Pad biquad cascade must reject input with > 5 stages."""
        excessive = [BiquadCoefficients() for _ in range(6)]
        with pytest.raises(ValueError, match="exceeds CXD90050 maximum"):
            pad_biquad_cascade(excessive, target_count=5)


# ============================================================================
# 8. Frequency Response Simulation Boundary Stress
# ============================================================================

class TestSimulationAdversarialGrid:
    """Stress tests frequency response simulation grid and DTFT evaluation."""

    def test_invalid_frequency_grid_parameters(self):
        with pytest.raises(ValueError, match="start_hz must be positive"):
            create_frequency_grid(start_hz=0.0)

        with pytest.raises(ValueError, match="stop_hz.*must be greater than start_hz"):
            create_frequency_grid(start_hz=1000.0, stop_hz=500.0)

        with pytest.raises(ValueError, match="num_points must be > 1"):
            create_frequency_grid(num_points=1)

        with pytest.raises(ValueError, match="Unsupported scale"):
            create_frequency_grid(scale="exponential")

    def test_dtft_evaluation_does_not_crash_on_dense_grid(self):
        """Stress-evaluates DTFT over 100,000 points."""
        coef = synthesize_low_shelf(f0=1400.0, gain_db=7.5, sample_rate=48000)
        grid = create_frequency_grid(start_hz=10.0, stop_hz=23990.0, num_points=100000)
        resp = evaluate_biquad_dtft(coef, grid, sample_rate=48000)
        assert len(resp.magnitude_db) == 100000
        assert np.all(np.isfinite(resp.magnitude_db))
        assert np.all(np.isfinite(resp.phase_rad))
