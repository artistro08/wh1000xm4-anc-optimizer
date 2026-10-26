# DSP Mathematics, Acoustic Theory & Fixed-Point Stability Proofs

This document provides the mathematical derivations and acoustic control-theory foundations behind the Biquad IIR filters synthesized by `xm4_optimizer`.

> **Scope and evidence.** The filter mathematics (RBJ cookbook shelves, Butterworth high-pass, Q4.28 quantization, pole-radius stability checks) is standard and is covered by the test suite. The mapping of these filters onto the headset is **not** established: on a real WH-1000XM4 the `ALIGN_*` items are 8-byte values, not 5-stage biquad blocks, and no ANC change has been measured (see [STATUS.md](STATUS.md) and [HARDWARE_AND_NVDM.md](HARDWARE_AND_NVDM.md)). Statements below about how the WH-1000XM4 uses `ALIGN_FF_*`/`ALIGN_FB_*` describe a model, not verified device behaviour. A stable biquad also does not prove that the whole electro-acoustic loop stays stable.

---

## 1. Hybrid ANC Control Loop & Bode's Sensitivity Integral

The Sony WH-1000XM4 implements a dual-sensor **Hybrid Active Noise Control (ANC)** topology per ear cup:
1. **Feedforward (FF) Path (`ALIGN_FF_L`, `ALIGN_FF_R`):** An exterior MEMS microphone samples ambient noise $x(n)$ before it penetrates the ear cup, filtering it through a 5-stage cascaded IIR equalizer $W_{\text{FF}}(z)$ to generate an anti-phase cancellation wave at the 40 mm driver.
2. **Feedback (FB) Path (`ALIGN_FB_L`, `ALIGN_FB_R`):** An interior error microphone positioned inside the ear cavity measures residual acoustic error $e(n)$ and closes a feedback loop $L(z) = P(z)\,W_{\text{FB}}(z)$.

By **Bode's Sensitivity Integral** for discrete-time causal linear time-invariant feedback loops:

$$\frac{1}{2\pi} \int_{-\pi}^{\pi} \ln |S(e^{j\omega})|\,d\omega = \sum_{|p_i| > 1} \ln |p_i| \ge 0, \quad S(z) = \frac{1}{1 + L(z)}$$

Aggressively boosting the **feedback** loop $W_{\text{FB}}(z)$ narrows the phase margin near the acoustic delay crossover ($1.5\text{ kHz} - 3.5\text{ kHz}$), causing waterbed amplification ($|S(e^{j\omega})| > 1$, heard as high-frequency hiss or howling oscillation).

In contrast, the **feedforward** path $W_{\text{FF}}(z)$ operates open-loop with respect to the interior error microphone (bounded only by physical cup-to-outer-mic acoustic leakage). Therefore, shaping `ALIGN_FF_L` and `ALIGN_FF_R` across **50 Hz – 800 Hz** increases low-frequency cancellation without eroding the feedback loop's phase margin.

---

## 2. Cascaded Biquad Transfer Function

Each CXD90050 alignment block executes $M = 5$ second-order IIR sections in series:

$$H(z) = G_0 \prod_{k=0}^{4} H_k(z), \quad H_k(z) = \frac{b_{0,k} + b_{1,k}z^{-1} + b_{2,k}z^{-2}}{1 + a_{1,k}z^{-1} + a_{2,k}z^{-2}}$$

In the time domain, [`BiquadFilter`](../xm4_optimizer/dsp/biquad.py) evaluates each section using **Transposed Direct Form II** to minimize internal state overflow near $z = 1$ (low-frequency poles):

$$\begin{aligned}
y[n] &= b_0 x[n] + s_1[n-1] \\
s_1[n] &= b_1 x[n] - a_1 y[n] + s_2[n-1] \\
s_2[n] &= b_2 x[n] - a_2 y[n]
\end{aligned}$$

---

## 3. Bilinear Transform & RBJ Filter Synthesis

### 3.1 Stage 0: 2nd-Order Low-Shelf Filter ($f_0 = 1400\text{ Hz}, G = +7.5\text{ dB}, S = 0.9$)

Given sampling rate $f_s$, corner frequency $f_0 < f_s/2$, shelf gain $G_{\text{dB}} \in [6.0, 9.0]$, and shelf slope $S = 0.9$:

$$A = 10^{G_{\text{dB}} / 40}, \quad \omega_0 = \frac{2\pi f_0}{f_s}, \quad \alpha = \frac{\sin\omega_0}{2} \sqrt{\left(A + \frac{1}{A}\right)\left(\frac{1}{S} - 1\right) + 2}, \quad \beta = 2\sqrt{A}\,\alpha$$

The unnormalized bilinear-transformed coefficients are:

$$\begin{aligned}
B_0 &= A\left((A+1) - (A-1)\cos\omega_0 + \beta\right) \\
B_1 &= 2A\left((A-1) - (A+1)\cos\omega_0\right) \\
B_2 &= A\left((A+1) - (A-1)\cos\omega_0 - \beta\right) \\
A_0 &= (A+1) + (A-1)\cos\omega_0 + \beta \\
A_1 &= -2\left((A-1) + (A+1)\cos\omega_0\right) \\
A_2 &= (A+1) + (A-1)\cos\omega_0 - \beta
\end{aligned}$$

Normalized by $A_0$:

$$(a_1, a_2, b_0, b_1, b_2) = \left(\frac{A_1}{A_0}, \frac{A_2}{A_0}, \frac{B_0}{A_0}, \frac{B_1}{A_0}, \frac{B_2}{A_0}\right)$$

Choosing $S = 0.9 < 1.0$ guarantees monotonic magnitude decay across the transition band without resonant overshoot.

### 3.2 Stage 1: 2nd-Order Butterworth Subsonic High-Pass Filter ($f_0 = 25\text{ Hz}, Q = 1/\sqrt{2}$)

To protect the 40 mm driver from infrasonic excursion saturation ($< 20\text{ Hz}$) when low-frequency shelf gain is boosted:

$$\omega_0 = \frac{2\pi f_0}{f_s}, \quad \alpha = \frac{\sin\omega_0}{2Q}, \quad Q = \frac{1}{\sqrt{2}}$$

$$\begin{aligned}
B_0 &= \frac{1 + \cos\omega_0}{2}, \quad B_1 = -(1 + \cos\omega_0), \quad B_2 = \frac{1 + \cos\omega_0}{2} \\
A_0 &= 1 + \alpha, \quad A_1 = -2\cos\omega_0, \quad A_2 = 1 - \alpha
\end{aligned}$$

---

## 4. Q4.28 Fixed-Point Quantization & Stability Invariance

### 4.1 Why Q4.28 Instead of Q1.31?

For low-frequency digital filters ($f_0 \ll f_s$), poles cluster near $z = +1$, yielding denominator coefficient $a_1 = -2r\cos\theta \approx -1.995$. Similarly, a $+7.5\text{ dB}$ shelf filter has numerator coefficients exceeding $1.0$ ($|b_1| \approx 1.77$).
Since Q1.31 can only represent $[-1.0, +0.9999999995]$, CXD90050 biquads use **signed 32-bit Q4.28** (`scale = 2^28 = 268,435,456`), spanning $[-8.0, +8.0 - 2^{-28}]$ with resolution $\Delta = 2^{-28} \approx 3.725 \times 10^{-9}$.

### 4.2 Schur-Cohn Triangle & Quantization Margin Proof

A 2nd-order discrete-time section with denominator $D(z) = z^2 + a_1 z + a_2$ has both poles strictly inside the open unit circle ($\max(|p_1|, |p_2|) < 1$) if and only if $(a_1, a_2)$ lies strictly inside the **Schur-Cohn / Jury stability triangle**:

$$\delta_{\text{param}} = \min(1 - |a_2|,\; 1 + a_2 - |a_1|) > 0$$

For Stage 1 ($f_0 = 25\text{ Hz}$ at $f_s = 48\text{ kHz}$, the closest pole pair to the unit circle):
- $a_1 = -1.99537239$, $a_2 = +0.99538309$
- Pole radius $r = \sqrt{a_2} = 0.99768888 < 1.0$ (pole margin $1 - r = 2.311 \times 10^{-3}$)
- Parameter margin $\delta_{\text{param}} = 1 + a_2 - |a_1| = 1.07 \times 10^{-5}$
- Ratio of stability margin to Q4.28 quantization step $\Delta = 2^{-28}$:
  $$\frac{\delta_{\text{param}}}{2^{-28}} \approx 2,872 \text{ LSBs of safety headroom}$$

[`verify_quantization_invariance`](../xm4_optimizer/dsp/stability.py) verifies this bound analytically before any coefficient is accepted.

---

## 5. References

1. S. M. Kuo and D. R. Morgan, *Active Noise Control Systems: Algorithms and DSP Implementations*, Wiley-Interscience, 1996.
2. S. J. Elliott, *Signal Processing for Active Control*, Academic Press, 2001.
3. R. Bristow-Johnson, *Cookbook Formulae for Audio EQ Biquad Filter Coefficients*, Audio Engineering Society (AES) / Music-DSP Archive.
4. V. Välimäki and J. D. Reiss, "All About Audio Equalization: Solutions and Frontiers," *Applied Sciences*, vol. 6, no. 5, p. 129, 2016.
5. A. V. Oppenheim and R. W. Schafer, *Discrete-Time Signal Processing*, 3rd ed., Prentice Hall, 2009 (Chapter 6: Finite Wordlength Effects in IIR Digital Filters).
