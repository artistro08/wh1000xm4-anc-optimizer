"""Comprehensive unit and integration test suite for xm4_optimizer.dsp.

Tests:
1. Fixed-point conversions (Q28, Q31), saturation clamping, struct packing.
2. Biquad models, sign conventions (Standard vs MAC), Transposed Direct Form II engine, cascades.
3. Characteristic roots, Schur-Cohn / Jury stability, stability margins, and quantization invariance.
4. RBJ filter synthesis (Low-Shelf, Peaking, High-Pass, multi-rate, ANC boost).
5. DTFT frequency simulation, attenuation band verification (+6 to +9 dB), and ASCII curve plotting.
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
    INT32_MAX,
    INT32_MIN,
    Q28_MAX_FLOAT,
    Q28_MIN_FLOAT,
    Q28_RESOLUTION,
    Q28_SCALE,
    Q31_MAX_FLOAT,
    Q31_MIN_FLOAT,
    Q31_RESOLUTION,
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
    AttenuationBandViolationError,
    BandVerificationResult,
    FrequencyResponse,
    assert_attenuation_band,
    create_frequency_grid,
    evaluate_alignment_block_dtft,
    evaluate_biquad_dtft,
    evaluate_cascade_dtft,
    render_ascii_response_curve,
    verify_attenuation_band,
)
from xm4_optimizer.dsp.stability import (
    FilterInstabilityError,
    PolePair,
    QuantizationStabilityReport,
    StabilityMargin,
    StabilityReport,
    assert_biquad_stable,
    check_schur_cohn,
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
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SHELF_F0,
    DEFAULT_SHELF_SLOPE,
    SUBSONIC_HPF_DEFAULT_F0,
    SUBSONIC_HPF_DEFAULT_Q,
    SUPPORTED_SAMPLE_RATES,
    TARGET_BAND_DEFAULT,
    SynthesisResult,
    calculate_biquad_poles,
    create_alignment_block,
    create_identity_biquad,
    pad_biquad_cascade,
    synthesize_anc_boost,
    synthesize_high_pass,
    synthesize_low_shelf,
    synthesize_peaking_eq,
)
from xm4_optimizer.nvdm.cxd90050 import CXD90050AlignmentBlock


# ==============================================================================
# 1. FIXED-POINT TESTS
# ==============================================================================

class TestFixedPointQ28:
    def test_q28_exact_boundaries(self):
        min_int = float_to_q28(Q28_MIN_FLOAT, clamp=True)
        assert min_int == INT32_MIN
        assert math.isclose(q28_to_float(min_int), -8.0, abs_tol=1e-9)

        max_int = float_to_q28(Q28_MAX_FLOAT, clamp=True)
        assert max_int == INT32_MAX
        assert math.isclose(q28_to_float(max_int), Q28_MAX_FLOAT, abs_tol=1e-8)

    def test_q28_clamping(self):
        assert float_to_q28(-9.5, clamp=True) == INT32_MIN
        assert float_to_q28(10.0, clamp=True) == INT32_MAX

    def test_q28_overflow_exception(self):
        with pytest.raises(OverflowError):
            float_to_q28(-8.000001, clamp=False)
        with pytest.raises(OverflowError):
            float_to_q28(8.0, clamp=False)

    def test_q28_non_finite_values(self):
        with pytest.raises(ValueError, match="non-finite"):
            float_to_q28(float("nan"))
        with pytest.raises(ValueError, match="non-finite"):
            float_to_q28(float("inf"))
        with pytest.raises(ValueError, match="non-finite"):
            float_to_q28(float("-inf"))

    def test_q28_precision(self):
        val = 1.0603721682
        q = float_to_q28(val)
        restored = q28_to_float(q)
        assert abs(val - restored) <= Q28_RESOLUTION


class TestFixedPointQ31:
    def test_q31_boundaries_unscaled(self):
        min_int = float_to_q31(-1.0, prescale=1, clamp=True)
        assert min_int == INT32_MIN
        assert math.isclose(q31_to_float(min_int, prescale=1), -1.0, abs_tol=1e-9)

        max_int = float_to_q31(Q31_MAX_FLOAT, prescale=1, clamp=True)
        assert max_int == INT32_MAX
        assert math.isclose(q31_to_float(max_int, prescale=1), Q31_MAX_FLOAT, abs_tol=1e-9)

    def test_q31_prescale_audio_pole(self):
        # Audio pole coefficient often |a1| > 1.0 (e.g. -1.7816724)
        a1 = -1.7816724087
        q = float_to_q31(a1, prescale=2)
        restored = q31_to_float(q, prescale=2)
        assert abs(a1 - restored) <= 2 * Q31_RESOLUTION

    def test_q31_invalid_prescale(self):
        with pytest.raises(ValueError, match="positive"):
            float_to_q31(0.5, prescale=0)
        with pytest.raises(ValueError, match="positive"):
            q31_to_float(100, prescale=-1)

    def test_q31_non_finite(self):
        with pytest.raises(ValueError, match="non-finite"):
            float_to_q31(float("nan"), prescale=1)


class TestBiquadPacking:
    def test_biquad_q28_pack_unpack(self):
        a1, a2, b0, b1, b2 = -1.78167, 0.80132, 1.06037, -1.76820, 0.75442
        data = pack_biquad_q28(a1, a2, b0, b1, b2)
        assert len(data) == 20
        unpacked = unpack_biquad_q28(data)
        for orig, res in zip((a1, a2, b0, b1, b2), unpacked):
            assert abs(orig - res) < 2e-8

    def test_unpack_invalid_length(self):
        with pytest.raises(ValueError, match="must be exactly 20 bytes"):
            unpack_biquad_q28(b"\x00" * 16)
        with pytest.raises(ValueError, match="must be exactly 20 bytes"):
            unpack_biquad_q28(b"\x00" * 24)

    def test_biquad_q31_pack_unpack(self):
        a1, a2, b0, b1, b2 = -1.78167, 0.80132, 1.06037, -1.76820, 0.75442
        data = pack_biquad_q31(a1, a2, b0, b1, b2, prescale=2)
        assert len(data) == 20
        unpacked = unpack_biquad_q31(data, prescale=2)
        for orig, res in zip((a1, a2, b0, b1, b2), unpacked):
            assert abs(orig - res) < 2e-9


# ==============================================================================
# 2. BIQUAD COEFFICIENT & ENGINE TESTS
# ==============================================================================

class TestBiquadCoefficients:
    def test_default_identity(self):
        b = BiquadCoefficients()
        assert b.a1 == 0.0 and b.a2 == 0.0
        assert b.b0 == 1.0 and b.b1 == 0.0 and b.b2 == 0.0
        assert b.sign_convention == SignConvention.STANDARD
        assert b.is_stable()
        assert math.isclose(b.magnitude_db(1000, 48000), 0.0, abs_tol=1e-12)

    def test_sign_convention_normalization(self):
        std = BiquadCoefficients(a1=-1.78, a2=0.80, b0=1.0, b1=-1.5, b2=0.5, sign_convention=SignConvention.STANDARD)
        mac = std.to_mac()
        assert mac.sign_convention == SignConvention.MAC_PRE_NEGATED
        assert math.isclose(mac.a1, 1.78)
        assert math.isclose(mac.a2, -0.80)
        assert mac.b0 == std.b0 and mac.b1 == std.b1 and mac.b2 == std.b2

        std2 = mac.to_standard()
        assert std2.sign_convention == SignConvention.STANDARD
        assert math.isclose(std2.a1, -1.78)
        assert math.isclose(std2.a2, 0.80)

    def test_sign_convention_auto_detection(self):
        assert detect_sign_convention(-1.78, 0.80) == SignConvention.STANDARD
        assert detect_sign_convention(1.78, -0.80) == SignConvention.MAC_PRE_NEGATED

    def test_pack_unpack_roundtrip(self):
        b = BiquadCoefficients(a1=-1.78167, a2=0.80132, b0=1.06037, b1=-1.76820, b2=0.75442)
        packed = b.pack()
        assert len(packed) == 20
        b_unpacked = BiquadCoefficients.unpack(packed)
        assert math.isclose(b.a1, b_unpacked.a1, abs_tol=2e-8)
        assert math.isclose(b.a2, b_unpacked.a2, abs_tol=2e-8)
        assert math.isclose(b.b0, b_unpacked.b0, abs_tol=2e-8)
        assert math.isclose(b.b1, b_unpacked.b1, abs_tol=2e-8)
        assert math.isclose(b.b2, b_unpacked.b2, abs_tol=2e-8)

    def test_poles_and_stability(self):
        b = BiquadCoefficients(a1=-1.78167, a2=0.80132)
        poles = b.poles()
        assert len(poles) == 2
        assert all(abs(p) < 1.0 for p in poles)
        assert b.is_stable()
        assert b.stability_margin() > 0

    def test_unstable_biquad(self):
        b_unstable = BiquadCoefficients(a1=2.5, a2=0.5)
        assert not b_unstable.is_stable()
        assert b_unstable.stability_margin() <= 0


class TestBiquadFilterAndCascade:
    def test_tdf2_filter_impulse_response_standard_vs_mac(self):
        coef_std = BiquadCoefficients(a1=-1.78, a2=0.80, b0=1.0, b1=-1.5, b2=0.5, sign_convention=SignConvention.STANDARD)
        coef_mac = coef_std.to_mac()

        f_std = BiquadFilter(coef_std)
        f_mac = BiquadFilter(coef_mac)

        impulse = [1.0] + [0.0] * 99
        y_std = f_std.process(impulse)
        y_mac = f_mac.process(impulse)

        for s, m in zip(y_std, y_mac):
            assert math.isclose(s, m, abs_tol=1e-12)

    def test_tdf2_step_response_dc_gain(self):
        # H(1) = (b0 + b1 + b2) / (1 + a1 + a2)
        b = BiquadCoefficients(a1=-0.5, a2=0.1, b0=2.0, b1=1.0, b2=0.5)
        expected_dc = (2.0 + 1.0 + 0.5) / (1.0 - 0.5 + 0.1)  # 3.5 / 0.6 = 5.8333333333

        filt = BiquadFilter(b)
        step_input = [1.0] * 100
        output = filt.process(step_input)
        assert math.isclose(output[-1], expected_dc, abs_tol=1e-5)

    def test_sign_mismatch_divergence(self):
        # Feeding MAC coefficients to Standard filter causes positive feedback explosion
        b_mac = BiquadCoefficients(a1=1.78, a2=-0.80, b0=1.0, b1=0.0, b2=0.0, sign_convention=SignConvention.STANDARD)
        filt = BiquadFilter(b_mac)
        impulse = [1.0] + [0.0] * 49
        out = filt.process(impulse)
        assert abs(out[49]) > 1e10

    def test_biquad_cascade_execution(self):
        b1 = BiquadCoefficients(a1=-0.5, a2=0.1, b0=1.5, b1=0.2, b2=0.1)
        b_identity = BiquadCoefficients()

        filt1 = BiquadFilter(b1)
        cascade = BiquadCascade([BiquadFilter(b1), BiquadFilter(b_identity), BiquadFilter(b_identity)])

        sig = [math.sin(2 * math.pi * 100 * n / 48000) for n in range(50)]
        out_single = filt1.process(sig)
        out_cascade = cascade.process(sig)

        for s, c in zip(out_single, out_cascade):
            assert math.isclose(s, c, abs_tol=1e-12)

    def test_cascade_reset(self):
        b = BiquadCoefficients(a1=-0.5, a2=0.1, b0=1.0, b1=0.0, b2=0.0)
        casc = BiquadCascade([BiquadFilter(b)])
        casc.process([1.0, 1.0, 1.0])
        casc.reset()
        # Processing zeros after reset should produce zeros
        out = casc.process([0.0] * 10)
        assert all(math.isclose(v, 0.0, abs_tol=1e-12) for v in out)

    def test_empty_cascade_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            BiquadCascade([])


# ==============================================================================
# 3. CHARACTERISTIC ROOTS & STABILITY TESTS
# ==============================================================================

class TestCharacteristicRootsAndPoles:
    def test_real_distinct_poles(self):
        # z^2 - 0.5z + 0.04 = (z - 0.4)(z - 0.1)
        roots = compute_characteristic_roots(a1=-0.5, a2=0.04)
        mags = sorted([abs(roots.p1), abs(roots.p2)])
        assert math.isclose(mags[0], 0.1, abs_tol=1e-12)
        assert math.isclose(mags[1], 0.4, abs_tol=1e-12)
        assert roots.max_radius == pytest.approx(0.4, abs=1e-12)

    def test_repeated_real_poles(self):
        # z^2 - 1.2z + 0.36 = (z - 0.6)^2
        roots = compute_characteristic_roots(a1=-1.2, a2=0.36)
        assert math.isclose(abs(roots.p1), 0.6, abs_tol=1e-12)
        assert math.isclose(abs(roots.p2), 0.6, abs_tol=1e-12)

    def test_complex_conjugate_poles(self):
        # Low-shelf at 1400 Hz: |p| = sqrt(a2)
        a1, a2 = -1.7816724, 0.8013175
        roots = compute_characteristic_roots(a1, a2)
        assert roots.is_complex_conjugate
        expected_mag = math.sqrt(a2)
        assert math.isclose(abs(roots.p1), expected_mag, abs_tol=1e-6)
        assert math.isclose(abs(roots.p2), expected_mag, abs_tol=1e-6)

    def test_origin_pass_through_poles(self):
        roots = compute_characteristic_roots(0.0, 0.0)
        assert roots.p1 == 0j and roots.p2 == 0j
        assert roots.max_radius == 0.0

    def test_degenerate_zero_a0_raises_error(self):
        with pytest.raises(ValueError, match="non-zero"):
            compute_characteristic_roots(1.0, 0.5, a0=0.0)

    def test_numpy_roots_exact_match(self):
        test_cases = [
            (-1.5, 0.7),
            (-0.3, -0.4),
            (0.8, 0.15),
            (-1.8, 0.95),
            (0.0, -0.81),
        ]
        for a1, a2 in test_cases:
            roots = compute_characteristic_roots(a1, a2)
            np_r = np.roots([1.0, a1, a2])
            calc_r = sorted([roots.p1, roots.p2], key=lambda x: (x.real, x.imag))
            np_sorted = sorted(np_r, key=lambda x: (x.real, x.imag))
            assert math.isclose(abs(calc_r[0] - np_sorted[0]), 0.0, abs_tol=1e-10)
            assert math.isclose(abs(calc_r[1] - np_sorted[1]), 0.0, abs_tol=1e-10)


class TestSchurCohnStabilityVerification:
    def test_interior_strictly_stable_filters(self):
        stable_points = [(0.0, 0.0), (0.0, 0.5), (-1.5, 0.8), (1.5, 0.8), (-1.78, 0.80)]
        for a1, a2 in stable_points:
            assert is_biquad_stable(a1, a2)
            assert check_schur_cohn(a1, a2)

    def test_boundary_unit_circle_poles_rejected(self):
        # Marginal stability: |poles| == 1.0 (undamped oscillator)
        assert not is_biquad_stable(0.0, 1.0)  # poles = +/- j
        assert not is_biquad_stable(2.0, 1.0)  # poles = -1, -1
        assert not is_biquad_stable(-2.0, 1.0)  # poles = +1, +1

    def test_exterior_divergent_poles_rejected(self):
        unstable_points = [(0.0, 1.05), (2.5, 0.5), (-2.5, 0.5), (0.0, -1.05), (2.0, 1.2)]
        for a1, a2 in unstable_points:
            assert not is_biquad_stable(a1, a2)
            assert not check_schur_cohn(a1, a2)

    def test_assert_biquad_stable_raises_exception(self):
        with pytest.raises(FilterInstabilityError) as exc_info:
            assert_biquad_stable(2.5, 0.5)
        assert "unstable" in str(exc_info.value)
        assert exc_info.value.max_pole_radius > 1.0

    def test_monte_carlo_1000_schur_cohn_vs_roots_equivalence(self):
        # Verify 1000 random points across parameter space
        rng = np.random.default_rng(42)
        a1_samples = rng.uniform(-3.0, 3.0, 1000)
        a2_samples = rng.uniform(-2.0, 2.0, 1000)

        for a1, a2 in zip(a1_samples, a2_samples):
            schur = check_schur_cohn(float(a1), float(a2), tolerance=1e-12)
            roots = np.roots([1.0, float(a1), float(a2)])
            geom = bool(np.all(np.abs(roots) < (1.0 - 1e-12)))
            assert schur == geom, f"Equivalence failed for a1={a1}, a2={a2}"


class TestStabilityMargins:
    def test_pole_margin_calculation(self):
        # Known pole radius 0.895163 -> margin 0.104837
        a1, a2 = -1.7816724, 0.8013175
        margin = compute_stability_margins(a1, a2)
        assert margin.is_strictly_stable
        assert math.isclose(margin.pole_margin, 1.0 - math.sqrt(a2), abs_tol=1e-5)

    def test_param_margin_calculation(self):
        a1, a2 = -1.7816724, 0.8013175
        margin = compute_stability_margins(a1, a2)
        cond1 = 1.0 - a2
        cond2 = 1.0 + a2 - abs(a1)
        expected_param = min(cond1, cond2)
        assert math.isclose(margin.param_margin, expected_param, abs_tol=1e-8)


class TestQuantizationInvariance:
    def test_q28_invariance_lowshelf_48k(self):
        ls = synthesize_low_shelf(1400.0, 7.5, 0.9, 48000)
        report = verify_quantization_invariance(ls.a1, ls.a2)
        assert report.is_invariant
        assert report.quantized_margin > 0.05
        assert report.pole_shift < 1e-6
        assert report.safety_headroom_ratio > 1000

    @pytest.mark.parametrize("fs", [96000, 192000, 768000])
    def test_q28_invariance_multi_rate(self, fs):
        ls = synthesize_low_shelf(1400.0, 7.5, 0.9, fs)
        report = verify_quantization_invariance(ls.a1, ls.a2)
        assert report.is_invariant
        assert report.pole_shift < 1e-6
        assert report.safety_headroom_ratio > 100


# ==============================================================================
# 4. SYNTHESIZER TESTS
# ==============================================================================

class TestSynthesizerLowShelf:
    def test_synthesize_low_shelf_boundaries(self):
        with pytest.raises(ValueError, match="positive"):
            synthesize_low_shelf(f0=0.0)
        with pytest.raises(ValueError, match="positive"):
            synthesize_low_shelf(sample_rate=-48000)
        with pytest.raises(ValueError, match="strictly less than Nyquist"):
            synthesize_low_shelf(f0=24000, sample_rate=48000)
        with pytest.raises(ValueError, match="positive"):
            synthesize_low_shelf(slope=0.0)

    @pytest.mark.parametrize("fs", [48000, 96000, 192000, 768000])
    def test_synthesize_low_shelf_multi_rate(self, fs):
        b = synthesize_low_shelf(f0=1400.0, gain_db=7.5, slope=0.9, sample_rate=fs)
        assert b.is_stable()
        # DC gain check: 20*log10((b0+b1+b2)/(1+a1+a2)) == 7.5 dB
        dc_gain = 20.0 * math.log10((b.b0 + b.b1 + b.b2) / (1.0 + b.a1 + b.a2))
        assert math.isclose(dc_gain, 7.5, abs_tol=1e-4)

        # 50 Hz and 800 Hz gain
        g_50 = b.magnitude_db(50.0, fs)
        g_800 = b.magnitude_db(800.0, fs)
        assert 6.0 <= g_50 <= 9.0
        assert 6.0 <= g_800 <= 9.0

    def test_synthesize_peaking_eq(self):
        b = synthesize_peaking_eq(f0=250.0, gain_db=7.5, q=1.0, sample_rate=48000)
        assert b.is_stable()
        # Peak gain at 250 Hz
        g_peak = b.magnitude_db(250.0, 48000)
        assert math.isclose(g_peak, 7.5, abs_tol=0.01)
        # 0 dB at DC and Nyquist
        assert math.isclose(b.magnitude_db(0.01, 48000), 0.0, abs_tol=0.05)
        assert math.isclose(b.magnitude_db(23990.0, 48000), 0.0, abs_tol=0.05)

    def test_synthesize_high_pass(self):
        b = synthesize_high_pass(f0=25.0, q=0.70710678, sample_rate=48000)
        assert b.is_stable()
        # 5 Hz should be heavily attenuated (< -20 dB)
        g_5hz = b.magnitude_db(5.0, 48000)
        assert g_5hz < -20.0
        # 50 Hz minimal attenuation
        g_50hz = b.magnitude_db(50.0, 48000)
        assert g_50hz > -0.5

    def test_dual_biquad_cascade(self):
        ls = synthesize_low_shelf(1400.0, 7.5, 0.9, 48000)
        hpf = synthesize_high_pass(25.0, 0.70710678, 48000)
        casc = [ls, hpf]

        # 5 Hz infrasonic rumble suppressed
        g_5hz = ls.magnitude_db(5.0, 48000) + hpf.magnitude_db(5.0, 48000)
        assert g_5hz < -15.0

        # In-band 50-800Hz gain maintained >= 6.0 dB
        for f in [50.0, 100.0, 200.0, 400.0, 600.0, 800.0]:
            tot_g = ls.magnitude_db(f, 48000) + hpf.magnitude_db(f, 48000)
            assert 6.0 <= tot_g <= 9.0

    def test_pad_biquad_cascade(self):
        stages = [synthesize_low_shelf()]
        padded = pad_biquad_cascade(stages, 5)
        assert len(padded) == 5
        assert padded[0] == stages[0]
        for p in padded[1:]:
            assert p == create_identity_biquad()

        # Over 5 stages raises
        with pytest.raises(ValueError, match="exceeds"):
            pad_biquad_cascade([create_identity_biquad()] * 6, 5)

    def test_create_alignment_block(self):
        stages = [synthesize_low_shelf()]
        block = create_alignment_block(filter_id=0, stages=stages, gain_db=1.5)
        assert isinstance(block, CXD90050AlignmentBlock)
        assert block.filter_id == 0
        assert block.gain_db == 1.5
        assert len(block.biquads) == 5
        packed = block.pack()
        assert len(packed) == 108

    def test_synthesize_anc_boost(self):
        res = synthesize_anc_boost(
            target_band=(50.0, 800.0),
            boost_gain_db=7.5,
            sample_rate=48000,
        )
        assert isinstance(res, SynthesisResult)
        assert res.is_stable
        assert res.max_pole_magnitude < 1.0
        assert len(res.coefficients) >= 1
        assert res.band_min_gain_db >= 6.0
        assert res.band_max_gain_db <= 9.0
        assert len(res.freqs) == len(res.magnitude_db)


# ==============================================================================
# 5. DTFT SIMULATION & BAND VERIFICATION TESTS
# ==============================================================================

class TestDTFTSimulation:
    def test_frequency_grid_generation(self):
        log_grid = create_frequency_grid(20.0, 20000.0, 100, scale="log")
        assert len(log_grid) == 100
        assert math.isclose(log_grid[0], 20.0)
        assert math.isclose(log_grid[-1], 20000.0)

        lin_grid = create_frequency_grid(20.0, 20000.0, 100, scale="linear")
        assert len(lin_grid) == 100

        with pytest.raises(ValueError):
            create_frequency_grid(-10.0, 1000.0)
        with pytest.raises(ValueError):
            create_frequency_grid(1000.0, 500.0)

    def test_identity_biquad_flat_response(self):
        ident = create_identity_biquad()
        grid = create_frequency_grid(20.0, 20000.0, 50)
        resp = evaluate_biquad_dtft(ident, grid, sample_rate=48000)
        assert np.allclose(resp.magnitude_db, 0.0, atol=1e-12)
        assert np.allclose(resp.phase_rad, 0.0, atol=1e-12)

    def test_dc_and_nyquist_frequency_gain(self):
        ls = synthesize_low_shelf(1400.0, 7.5, 0.9, 48000)
        # DC frequency (approaching 0)
        grid = np.array([0.001, 23999.0])
        resp = evaluate_biquad_dtft(ls, grid, 48000)
        assert math.isclose(resp.magnitude_db[0], 7.5, abs_tol=1e-3)
        assert math.isclose(resp.magnitude_db[1], 0.0, abs_tol=1e-2)

    def test_evaluate_cascade_dtft(self):
        b1 = synthesize_peaking_eq(500.0, 3.0, 1.0, 48000)
        b2 = synthesize_peaking_eq(500.0, 3.0, 1.0, 48000)
        grid = np.array([500.0])
        resp = evaluate_cascade_dtft([b1, b2], grid, 48000, gain_db=0.0)
        # 3 dB + 3 dB = 6 dB
        assert math.isclose(resp.magnitude_db[0], 6.0, abs_tol=0.02)

    def test_evaluate_alignment_block_dtft(self):
        ls = synthesize_low_shelf()
        block = create_alignment_block(0, [ls], gain_db=0.0)
        resp = evaluate_alignment_block_dtft(block, sample_rate=48000)
        assert isinstance(resp, FrequencyResponse)
        # Check gain at 200 Hz
        gain_200 = resp.get_gain_at(200.0)
        assert 7.0 <= gain_200 <= 8.0

    def test_validate_alignment_block_stability(self):
        ls = synthesize_low_shelf()
        block = create_alignment_block(0, [ls])
        reports = validate_alignment_block_stability(block)
        assert len(reports) == 5
        assert all(r.is_stable for r in reports)


class TestAttenuationBandVerification:
    def test_nominal_lowshelf_satisfies_acceptance_r2(self):
        ls = synthesize_low_shelf(1400.0, 7.5, 0.9, 48000)
        grid = create_frequency_grid(20.0, 20000.0, 500)
        resp = evaluate_biquad_dtft(ls, grid, 48000)

        result = verify_attenuation_band(resp, (50.0, 800.0), min_gain_db=6.0, max_gain_db=9.0)
        assert result.passed
        assert result.min_gain_db >= 6.0
        assert result.max_gain_db <= 9.0

        # assert_attenuation_band should not raise
        res2 = assert_attenuation_band(resp, (50.0, 800.0), min_gain_db=6.0, max_gain_db=9.0)
        assert res2.passed

    def test_insufficient_gain_fails_assertion(self):
        ls_weak = synthesize_low_shelf(1400.0, 4.0, 0.9, 48000)  # Only +4 dB
        grid = create_frequency_grid(20.0, 20000.0, 500)
        resp = evaluate_biquad_dtft(ls_weak, grid, 48000)

        result = verify_attenuation_band(resp, (50.0, 800.0), min_gain_db=6.0, max_gain_db=9.0)
        assert not result.passed
        assert "Min gain" in result.failure_reason

        with pytest.raises(AttenuationBandViolationError):
            assert_attenuation_band(resp, (50.0, 800.0), min_gain_db=6.0, max_gain_db=9.0)

    def test_excessive_gain_fails_assertion(self):
        ls_strong = synthesize_low_shelf(1400.0, 11.0, 0.9, 48000)  # +11 dB
        grid = create_frequency_grid(20.0, 20000.0, 500)
        resp = evaluate_biquad_dtft(ls_strong, grid, 48000)

        result = verify_attenuation_band(resp, (50.0, 800.0), min_gain_db=6.0, max_gain_db=9.0)
        assert not result.passed
        assert "Max gain" in result.failure_reason

        with pytest.raises(AttenuationBandViolationError):
            assert_attenuation_band(resp, (50.0, 800.0), min_gain_db=6.0, max_gain_db=9.0)


class TestAsciiCurveRenderer:
    def test_ascii_curve_string_formatting(self):
        freqs = np.geomspace(20.0, 20000.0, 100)
        mags = np.linspace(7.5, 0.0, 100)
        rendered = render_ascii_response_curve(freqs, mags, width=50, height=10)
        assert "Frequency Response Curve" in rendered
        assert "┌" in rendered and "┐" in rendered
        assert "└" in rendered and "┘" in rendered
        assert "■" in rendered or "*" in rendered

    def test_ascii_curve_custom_dimensions(self):
        freqs = np.geomspace(20.0, 20000.0, 100)
        mags = np.ones(100) * 5.0
        rendered = render_ascii_response_curve(freqs, mags, width=40, height=8)
        lines = rendered.splitlines()
        # header + top border + 8 rows + bottom border + labels = ~13 lines
        assert len(lines) >= 11

    def test_ascii_curve_empty_input(self):
        rendered = render_ascii_response_curve(np.array([]), np.array([]))
        assert "Empty" in rendered
