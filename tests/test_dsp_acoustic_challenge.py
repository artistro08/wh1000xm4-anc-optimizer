"""Empirical Adversarial Stress Test Suite: Acoustic Frequency Response & Synthesis.

Authored by: challenger_m2_2 (Empirical Challenger)
Focus Areas:
1. Continuous frequency grid attenuation depth verification (50 Hz - 800 Hz, +6.0 to +9.0 dB).
2. Multi-rate numerical stability and extreme oversampling (48k, 96k, 192k, 768k).
3. 5-Stage CXD90050 cascade time-domain dynamics (step, impulse, square wave, linearity, reset).
4. Sign convention mismatch injection, exponential blowout prevention, and auto-detection recovery.
"""

from __future__ import annotations

import cmath
import math
from typing import List, Sequence
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
    Q28_RESOLUTION,
    float_to_q28,
    q28_to_float,
)
from xm4_optimizer.dsp.simulation import (
    AttenuationBandViolationError,
    BandVerificationResult,
    assert_attenuation_band,
    create_frequency_grid,
    evaluate_alignment_block_dtft,
    evaluate_biquad_dtft,
    evaluate_cascade_dtft,
    verify_attenuation_band,
)
from xm4_optimizer.dsp.stability import (
    FilterInstabilityError,
    assert_biquad_stable,
    compute_characteristic_roots,
    compute_stability_margins,
    is_biquad_stable,
    validate_alignment_block_stability,
    verify_quantization_invariance,
)
from xm4_optimizer.dsp.synthesizer import (
    ATTENUATION_MAX_ALLOWED_DB,
    ATTENUATION_MIN_REQUIRED_DB,
    CXD90050_BIQUAD_COUNT,
    DEFAULT_BOOST_GAIN_DB,
    DEFAULT_SHELF_F0,
    DEFAULT_SHELF_SLOPE,
    SUBSONIC_HPF_DEFAULT_F0,
    SUPPORTED_SAMPLE_RATES,
    TARGET_BAND_DEFAULT,
    create_alignment_block,
    pad_biquad_cascade,
    synthesize_anc_boost,
    synthesize_high_pass,
    synthesize_low_shelf,
    synthesize_peaking_eq,
)
from xm4_optimizer.nvdm.cxd90050 import CXD90050AlignmentBlock


# ==============================================================================
# 1. CONTINUOUS FREQUENCY GRID ATTENUATION DEPTH (50 Hz - 800 Hz)
# ==============================================================================

class TestContinuousFrequencyGridAttenuation:
    """Stress tests continuous frequency grid attenuation across [50 Hz, 800 Hz]."""

    @pytest.mark.parametrize("fs", SUPPORTED_SAMPLE_RATES)
    @pytest.mark.parametrize("cascade_subsonic", [False, True])
    def test_continuous_dense_grid_100k_points_in_bounds(
        self, fs: int, cascade_subsonic: bool
    ):
        """Evaluates 100,000 points across [50 Hz, 800 Hz] asserting [6.0, 9.0] dB."""
        res = synthesize_anc_boost(sample_rate=fs, cascade_subsonic=cascade_subsonic)
        assert res.is_stable
        assert res.max_pole_magnitude < 1.0

        # High-density linear grid (100,000 points)
        f_dense = np.linspace(50.0, 800.0, 100000)
        resp = evaluate_cascade_dtft(res.coefficients, f_dense, sample_rate=fs)

        min_gain = float(np.min(resp.magnitude_db))
        max_gain = float(np.max(resp.magnitude_db))

        assert min_gain >= ATTENUATION_MIN_REQUIRED_DB - 1e-4, (
            f"fs={fs}, sub={cascade_subsonic}: Min gain {min_gain:.4f} dB < 6.0 dB"
        )
        assert max_gain <= ATTENUATION_MAX_ALLOWED_DB + 1e-4, (
            f"fs={fs}, sub={cascade_subsonic}: Max gain {max_gain:.4f} dB > 9.0 dB"
        )

    @pytest.mark.parametrize("fs", SUPPORTED_SAMPLE_RATES)
    def test_continuous_log_grid_100k_points_in_bounds(self, fs: int):
        """Evaluates 100,000 log-spaced points across [50 Hz, 800 Hz]."""
        res = synthesize_anc_boost(sample_rate=fs, cascade_subsonic=False)
        f_log = np.geomspace(50.0, 800.0, 100000)
        resp = evaluate_cascade_dtft(res.coefficients, f_log, sample_rate=fs)

        min_gain = float(np.min(resp.magnitude_db))
        max_gain = float(np.max(resp.magnitude_db))

        assert min_gain >= 6.0
        assert max_gain <= 9.0

    @pytest.mark.parametrize("fs", SUPPORTED_SAMPLE_RATES)
    def test_band_edges_exact_boundary(self, fs: int):
        """Evaluates exact boundary frequencies: 50.0 Hz, 50.001 Hz, 799.999 Hz, 800.0 Hz."""
        res = synthesize_anc_boost(sample_rate=fs, cascade_subsonic=False)
        edges = np.array([50.0, 50.001, 799.999, 800.0])
        resp = evaluate_cascade_dtft(res.coefficients, edges, sample_rate=fs)

        for f, g in zip(edges, resp.magnitude_db):
            assert 6.0 <= g <= 9.0, f"Edge f={f} Hz produced gain {g:.4f} dB outside [6.0, 9.0]"

    @pytest.mark.parametrize("fs", SUPPORTED_SAMPLE_RATES)
    def test_smooth_monotonic_shelf_roll_off(self, fs: int):
        """Ensures that the low shelf has no ripple or positive slope in the passband."""
        res = synthesize_anc_boost(sample_rate=fs, cascade_subsonic=False)
        f_eval = np.linspace(50.0, 800.0, 10000)
        resp = evaluate_cascade_dtft(res.coefficients, f_eval, sample_rate=fs)

        # Difference between successive points should be negative (monotonically rolling off)
        diffs = np.diff(resp.magnitude_db)
        # Low-shelf transition: gain at 50 Hz (~7.5 dB) decreases smoothly toward 800 Hz (~6.5 dB)
        assert np.all(diffs <= 1e-6), (
            f"Non-monotonic ripple detected in low shelf at fs={fs}: max diff={np.max(diffs):.2e}"
        )

    def test_out_of_band_transparency_decay(self):
        """Verifies high-frequency transparency (shelf rolls off toward 0 dB)."""
        res = synthesize_anc_boost(sample_rate=48000)
        hf_points = np.array([2000.0, 5000.0, 10000.0, 20000.0])
        resp = evaluate_cascade_dtft(res.coefficients, hf_points, sample_rate=48000)

        # At 2 kHz: gain should be substantially lower than passband (< 5.0 dB)
        assert resp.magnitude_db[0] < 5.0
        # At 10 kHz: gain should be close to 0 dB (< 1.0 dB)
        assert resp.magnitude_db[2] < 1.0
        # At 20 kHz: gain should be < 0.5 dB
        assert resp.magnitude_db[3] < 0.5

    def test_non_compliant_attenuation_band_raises_violation_error(self):
        """Tests that assert_attenuation_band properly raises AttenuationBandViolationError."""
        # Synthesize a filter with only +3.0 dB gain (non-compliant)
        weak_filter = synthesize_low_shelf(f0=1400.0, gain_db=3.0, sample_rate=48000)
        grid = create_frequency_grid(start_hz=20.0, stop_hz=20000.0, num_points=500)
        resp = evaluate_cascade_dtft([weak_filter], grid, sample_rate=48000)

        with pytest.raises(AttenuationBandViolationError) as exc_info:
            assert_attenuation_band(resp, band=(50.0, 800.0), min_gain_db=6.0, max_gain_db=9.0)

        err = exc_info.value
        assert err.band == (50.0, 800.0)
        assert err.min_gain_db < 6.0
        assert "Min gain" in str(err)


# ==============================================================================
# 2. MULTI-RATE & HIGH-RATE (768 kHz) NUMERICAL STABILITY
# ==============================================================================

class TestMultiRateNumericalStability:
    """Stress tests numerical behavior under multi-rate and 768 kHz extreme oversampling."""

    @pytest.mark.parametrize("fs", SUPPORTED_SAMPLE_RATES)
    def test_multi_rate_pole_stability_and_margins(self, fs: int):
        """Verifies pole stability margins across all supported sample rates."""
        res = synthesize_anc_boost(sample_rate=fs, cascade_subsonic=False)
        assert res.is_stable
        assert res.max_pole_magnitude < 1.0

        for b in res.coefficients:
            margin = compute_stability_margins(b.a1, b.a2)
            assert margin.is_strictly_stable
            assert margin.pole_margin > 0.0
            assert margin.param_margin > 0.0

    def test_768khz_extreme_oversampling_low_shelf_stability(self):
        """At fs = 768 kHz, omega_0 = 2*pi*1400/768000 = 0.01145 rad (close to 0)."""
        fs = 768000
        ls = synthesize_low_shelf(f0=1400.0, gain_db=7.5, slope=0.9, sample_rate=fs)

        roots = compute_characteristic_roots(ls.a1, ls.a2)
        assert roots.max_radius < 1.0
        assert is_biquad_stable(ls.a1, ls.a2)

        # Quantization invariance under Q28
        q_rep = verify_quantization_invariance(ls.a1, ls.a2, format_bits=28)
        assert q_rep.is_invariant
        assert q_rep.quantized_margin > 0.0
        assert q_rep.pole_shift < 1e-5

    def test_768khz_extreme_oversampling_25hz_hpf_stability(self):
        """At fs = 768 kHz and f0 = 25 Hz, omega_0 = 2*pi*25/768000 = 0.0002045 rad."""
        fs = 768000
        hp = synthesize_high_pass(f0=25.0, q=0.70710678, sample_rate=fs)

        roots = compute_characteristic_roots(hp.a1, hp.a2)
        assert roots.max_radius < 1.0
        assert is_biquad_stable(hp.a1, hp.a2)

        # Check stability margin against Q28 LSB (3.725e-9)
        margin = compute_stability_margins(hp.a1, hp.a2)
        assert margin.param_margin > Q28_RESOLUTION, (
            f"HPF margin {margin.param_margin:.2e} underflows Q28 resolution {Q28_RESOLUTION:.2e}"
        )

        q_rep = verify_quantization_invariance(hp.a1, hp.a2, format_bits=28)
        assert q_rep.is_invariant
        assert q_rep.quantized_margin > 0.0

    @pytest.mark.parametrize("fs", SUPPORTED_SAMPLE_RATES)
    def test_q28_roundtrip_precision_preservation(self, fs: int):
        """Ensures that Q28 packing/unpacking retains biquad stability and accuracy."""
        res = synthesize_anc_boost(sample_rate=fs)
        b = res.coefficients[0]

        packed = b.pack()
        assert len(packed) == 20
        unpacked = BiquadCoefficients.unpack(packed)

        assert abs(unpacked.a1 - b.a1) <= Q28_RESOLUTION
        assert abs(unpacked.a2 - b.a2) <= Q28_RESOLUTION
        assert abs(unpacked.b0 - b.b0) <= Q28_RESOLUTION
        assert abs(unpacked.b1 - b.b1) <= Q28_RESOLUTION
        assert abs(unpacked.b2 - b.b2) <= Q28_RESOLUTION
        assert unpacked.is_stable()


# ==============================================================================
# 3. 5-STAGE CXD90050 CASCADE STABILITY & TIME-DOMAIN DYNAMICS
# ==============================================================================

class TestCascadeStabilityAndTimeDomainDynamics:
    """Stress tests the 5-stage CXD90050 biquad cascade in time domain."""

    @pytest.mark.parametrize("fs", SUPPORTED_SAMPLE_RATES)
    def test_5_stage_cascade_step_response_dc_gain_accuracy(self, fs: int):
        """Feeds unit step [1.0, 1.0, ...] and checks exact match to theoretical DC gain."""
        res = synthesize_anc_boost(sample_rate=fs, cascade_subsonic=False)
        block = create_alignment_block(filter_id=0, stages=res.coefficients)
        assert len(block.biquads) == CXD90050_BIQUAD_COUNT

        cascade = BiquadCascade([BiquadFilter(b) for b in block.biquads])

        # Theoretical DC gain: H(z=1) = (b0 + b1 + b2) / (1 + a1 + a2)
        ls = res.coefficients[0].to_standard()
        h_dc_theoretical = (ls.b0 + ls.b1 + ls.b2) / (1.0 + ls.a1 + ls.a2)
        h_dc_db = 20.0 * math.log10(abs(h_dc_theoretical))
        assert pytest.approx(h_dc_db, abs=1e-4) == DEFAULT_BOOST_GAIN_DB

        # Process step input through cascade
        n_samples = 40000
        step_in = np.ones(n_samples, dtype=np.float64)
        step_out = cascade.process(step_in)

        # Output after settling must equal theoretical DC gain
        final_val = step_out[-1]
        assert abs(final_val - h_dc_theoretical) < 1e-9, (
            f"Step DC gain error at fs={fs}: sim={final_val:.8f}, theo={h_dc_theoretical:.8f}"
        )

    @pytest.mark.parametrize("fs", SUPPORTED_SAMPLE_RATES)
    def test_5_stage_cascade_impulse_response_zero_ringing(self, fs: int):
        """Feeds unit impulse [1.0, 0.0, ...] and asserts tail decay to 0 (no ringing/blowout)."""
        res = synthesize_anc_boost(sample_rate=fs, cascade_subsonic=False)
        block = create_alignment_block(filter_id=0, stages=res.coefficients)
        cascade = BiquadCascade([BiquadFilter(b) for b in block.biquads])

        n_samples = 40000
        impulse = np.zeros(n_samples, dtype=np.float64)
        impulse[0] = 1.0

        imp_out = np.array(cascade.process(impulse))

        # Check finite energy (BIBO stability)
        total_energy = float(np.sum(imp_out ** 2))
        assert np.isfinite(total_energy)
        assert total_energy > 0.0

        # Check tail decays completely to 0.0
        tail = imp_out[n_samples // 2 :]
        max_tail = float(np.max(np.abs(tail)))
        assert max_tail < 1e-12, f"Ringing detected at fs={fs}: max tail={max_tail:.2e}"

    def test_cascade_linearity_superposition(self):
        """Verifies linearity: H(a*x1 + b*x2) == a*H(x1) + b*H(x2)."""
        res = synthesize_anc_boost(sample_rate=48000)
        block = create_alignment_block(filter_id=0, stages=res.coefficients)

        cascade1 = BiquadCascade([BiquadFilter(b) for b in block.biquads])
        cascade2 = BiquadCascade([BiquadFilter(b) for b in block.biquads])
        cascade3 = BiquadCascade([BiquadFilter(b) for b in block.biquads])

        np.random.seed(123)
        n = 1000
        x1 = np.random.randn(n)
        x2 = np.random.randn(n)
        a, b = 2.5, -1.8

        y1 = np.array(cascade1.process(x1))
        y2 = np.array(cascade2.process(x2))
        y_comb = np.array(cascade3.process(a * x1 + b * x2))

        expected = a * y1 + b * y2
        max_err = float(np.max(np.abs(y_comb - expected)))
        assert max_err < 1e-12, f"Linearity violation: max_err={max_err:.2e}"

    def test_cascade_reset_clears_internal_registers(self):
        """Verifies that cascade.reset() resets all stages to zero state."""
        res = synthesize_anc_boost(sample_rate=48000)
        block = create_alignment_block(filter_id=0, stages=res.coefficients)
        cascade = BiquadCascade([BiquadFilter(b) for b in block.biquads])

        # Excite filter with heavy signal
        cascade.process(np.ones(500))
        # Active low-shelf filter must have non-zero internal states
        assert abs(cascade.filters[0].s1) > 0.0 or abs(cascade.filters[0].s2) > 0.0

        # Reset
        cascade.reset()
        for f in cascade.filters:
            assert f.s1 == 0.0
            assert f.s2 == 0.0

        # Output to zero input must be strictly zero
        zero_out = cascade.process(np.zeros(100))
        assert all(v == 0.0 for v in zero_out)


# ==============================================================================
# 4. SIGN MISMATCH HANDLING & EXPONENTIAL BLOWOUT PREVENTION
# ==============================================================================

class TestSignMismatchHandling:
    """Stress tests sign convention handling (Standard vs MAC) and blowout prevention."""

    def test_standard_vs_mac_identical_time_domain_filtering(self):
        """STANDARD and MAC_PRE_NEGATED implementations must yield identical output."""
        std_coef = synthesize_low_shelf(sample_rate=48000)
        mac_coef = std_coef.to_mac()

        assert std_coef.sign_convention == SignConvention.STANDARD
        assert mac_coef.sign_convention == SignConvention.MAC_PRE_NEGATED
        assert mac_coef.a1 == -std_coef.a1
        assert mac_coef.a2 == -std_coef.a2

        filt_std = BiquadFilter(std_coef)
        filt_mac = BiquadFilter(mac_coef)

        np.random.seed(456)
        signal = np.random.randn(2000).tolist()

        y_std = filt_std.process(signal)
        y_mac = filt_mac.process(signal)

        diff = np.max(np.abs(np.array(y_std) - np.array(y_mac)))
        assert diff == 0.0, f"Standard vs MAC execution mismatch: diff={diff}"

    def test_sign_mismatch_causes_exponential_divergence_when_unhandled(self):
        """Demonstrates that feeding MAC coefficients into a STANDARD filter explodes."""
        std_coef = synthesize_low_shelf(sample_rate=48000)
        mac_coef = std_coef.to_mac()

        # Artificially mislabel MAC coefficients as STANDARD (sign mismatch injection)
        mismatched = BiquadCoefficients(
            a1=mac_coef.a1,  # Positive value (~1.78)
            a2=mac_coef.a2,  # Negative value (~ -0.80)
            b0=mac_coef.b0,
            b1=mac_coef.b1,
            b2=mac_coef.b2,
            sign_convention=SignConvention.STANDARD,  # Mismatched!
        )

        # Must have poles outside unit circle
        poles = mismatched.poles()
        assert any(abs(p) > 1.0 for p in poles)
        assert not mismatched.is_stable()

        # Running through filter explodes
        bad_filt = BiquadFilter(mismatched)
        imp_resp = bad_filt.process([1.0] + [0.0] * 50)
        # Sample 50 should diverge exponentially (> 10^10)
        assert abs(imp_resp[-1]) > 1e10, "Mismatched filter failed to diverge as expected"

    def test_stability_engine_catches_sign_mismatched_coefficients(self):
        """Verifies that assert_biquad_stable catches sign mismatch and raises FilterInstabilityError."""
        std_coef = synthesize_low_shelf(sample_rate=48000)
        mac_coef = std_coef.to_mac()

        mismatched = BiquadCoefficients(
            a1=mac_coef.a1,
            a2=mac_coef.a2,
            b0=mac_coef.b0,
            b1=mac_coef.b1,
            b2=mac_coef.b2,
            sign_convention=SignConvention.STANDARD,
        )

        with pytest.raises(FilterInstabilityError) as exc_info:
            assert_biquad_stable(mismatched)

        err = exc_info.value
        assert err.max_pole_radius > 1.0
        assert "unstable" in str(err)

    def test_auto_detect_sign_convention_rescues_mismatched_filter(self):
        """Verifies that detect_sign_convention correctly identifies MAC and rescues the filter."""
        std_coef = synthesize_low_shelf(sample_rate=48000)
        mac_coef = std_coef.to_mac()

        # Detect true nature from raw a1, a2
        detected = detect_sign_convention(mac_coef.a1, mac_coef.a2)
        assert detected == SignConvention.MAC_PRE_NEGATED

        # Rescue by assigning correct convention and converting to standard
        rescued = BiquadCoefficients(
            a1=mac_coef.a1,
            a2=mac_coef.a2,
            b0=mac_coef.b0,
            b1=mac_coef.b1,
            b2=mac_coef.b2,
            sign_convention=detected,
        ).to_standard()

        assert rescued.is_stable()
        rep = assert_biquad_stable(rescued)
        assert rep.is_stable

    def test_sign_convention_roundtrip_idempotency(self):
        """Tests that repeated conversions between conventions are lossless and idempotent."""
        std = synthesize_low_shelf(sample_rate=48000)

        # Standard -> MAC -> Standard
        roundtrip = std.to_mac().to_standard()
        assert roundtrip.sign_convention == SignConvention.STANDARD
        assert roundtrip.a1 == std.a1
        assert roundtrip.a2 == std.a2

        # MAC -> Standard -> MAC
        mac = std.to_mac()
        mac_roundtrip = mac.to_standard().to_mac()
        assert mac_roundtrip.sign_convention == SignConvention.MAC_PRE_NEGATED
        assert mac_roundtrip.a1 == mac.a1
        assert mac_roundtrip.a2 == mac.a2
