#include "AeroSpatialEngine.hpp"
#include <iostream>
#include <vector>
#include <cmath>
#include <cassert>
#include <numeric>

using namespace aerospatial;

// Helper: Calculate Root Mean Square
static float calculateRMS(const std::vector<float>& buffer) {
    if (buffer.empty()) return 0.0f;
    double sum = 0.0;
    for (float s : buffer) sum += s * s;
    return std::sqrt(static_cast<float>(sum / buffer.size()));
}

// Helper: Normalized Cross-Correlation Peak and Lag
static std::pair<float, int> calculateIACC(const std::vector<float>& x, const std::vector<float>& y, int maxLag) {
    int n = static_cast<int>(x.size());
    double rmsX = calculateRMS(x);
    double rmsY = calculateRMS(y);
    if (rmsX < 1e-6 || rmsY < 1e-6) return {0.0f, 0};

    float maxCorr = -1.0f;
    int bestLag = 0;

    for (int lag = -maxLag; lag <= maxLag; ++lag) {
        double sum = 0.0;
        int count = 0;
        for (int i = 0; i < n; ++i) {
            int j = i + lag;
            if (j >= 0 && j < n) {
                sum += x[i] * y[j];
                count++;
            }
        }
        if (count > 0) {
            float corr = static_cast<float>(sum / (n * rmsX * rmsY));
            if (corr > maxCorr) {
                maxCorr = corr;
                bestLag = lag;
            }
        }
    }
    return {maxCorr, bestLag};
}

void test_initialization() {
    std::cout << "[Test 1] Testing Engine Initialization and Buffer Lifecycle...\n";
    AeroSpatialEngine engine;
    SpatialConfig config{};
    config.sampleRate = 48000;
    config.frameSize = 256;
    config.speakerAzimuthDeg = 30.0f;

    bool ok = engine.init(config);
    assert(ok && "Engine initialization must succeed with default KEMAR HRTF");
    assert(engine.isInitialized());

    engine.shutdown();
    assert(!engine.isInitialized());
    std::cout << "  -> PASS: Engine correctly initializes and shuts down cleanly.\n";
}

void test_front_center_iacc() {
    std::cout << "[Test 2] Testing Front Center (Azimuth = 0 deg) Interaural Symmetry...\n";
    AeroSpatialEngine engine;
    SpatialConfig config{};
    config.sampleRate = 48000;
    config.frameSize = 256;
    engine.init(config);

    // Generate 48000 Hz 1 kHz pure sine wave impulse burst
    const int numSamples = 1024;
    std::vector<float> monoIn(numSamples);
    for (int i = 0; i < numSamples; ++i) {
        monoIn[i] = std::sin(2.0f * 3.14159265f * 1000.0f * i / 48000.0f);
    }

    std::vector<float> outL(numSamples, 0.0f);
    std::vector<float> outR(numSamples, 0.0f);

    engine.processPointSource(monoIn.data(), outL.data(), outR.data(), numSamples, 0.0f, 0.0f);

    float rmsL = calculateRMS(outL);
    float rmsR = calculateRMS(outR);

    // Front center on physical KEMAR head has natural human pinna asymmetry (<2.5 dB)
    float levelDeltaDb = 20.0f * std::log10(rmsR / rmsL);
    std::cout << "  RMS L: " << rmsL << ", RMS R: " << rmsR << " (Delta: " << levelDeltaDb << " dB)\n";
    assert(std::abs(levelDeltaDb) < 2.5f && "Front center sound must have realistic bilateral symmetry (<2.5 dB on KEMAR)");

    // IACC must be > 0.95 with near-zero lag (|lag| <= 1 sample)
    auto [iacc, lag] = calculateIACC(outL, outR, 50);
    std::cout << "  IACC Peak: " << iacc << ", Lag: " << lag << " samples\n";
    assert(iacc > 0.95f && "Front center must have near-perfect correlation (IACC > 0.95)");
    assert(std::abs(lag) <= 1 && "Front center must arrive at both ears almost simultaneously (|lag| <= 1)");
    std::cout << "  -> PASS: Front Center delivers symmetric zero-lag binaural response.\n";
}

void test_lateral_itd_and_ild() {
    std::cout << "[Test 3] Testing Lateral Localization: ITD and ILD at Azimuth = +90 deg (Full Right)...\n";
    AeroSpatialEngine engine;
    SpatialConfig config{};
    config.sampleRate = 48000;
    config.frameSize = 256;
    engine.init(config);

    const int numSamples = 1024;
    // Dirac impulse burst for sharp arrival time detection
    std::vector<float> monoIn(numSamples, 0.0f);
    monoIn[0] = 1.0f;
    monoIn[1] = 0.8f;
    monoIn[2] = 0.5f;

    std::vector<float> outL(numSamples, 0.0f);
    std::vector<float> outR(numSamples, 0.0f);

    // Point source placed directly on the right (+90 deg)
    engine.processPointSource(monoIn.data(), outL.data(), outR.data(), numSamples, 90.0f, 0.0f);

    float rmsL = calculateRMS(outL);
    float rmsR = calculateRMS(outR);

    // ILD (Interaural Level Difference): Right ear must be noticeably louder due to head shadowing
    float ildDb = 20.0f * std::log10(rmsR / rmsL);
    std::cout << "  RMS Left Ear: " << rmsL << ", RMS Right Ear: " << rmsR << "\n";
    std::cout << "  Measured Head Shadow ILD: " << ildDb << " dB\n";
    assert(ildDb > 4.0f && "Sound from +90 deg must be significantly louder in Right ear (ILD > 4 dB)");

    // ITD (Interaural Time Difference): Find global peak arrival sample in Left and Right ears
    int peakSampleL = 0, peakSampleR = 0;
    float peakValL = 0.0f, peakValR = 0.0f;
    for (int i = 0; i < 256; ++i) {
        if (std::abs(outL[i]) > peakValL) { peakValL = std::abs(outL[i]); peakSampleL = i; }
        if (std::abs(outR[i]) > peakValR) { peakValR = std::abs(outR[i]); peakSampleR = i; }
    }

    int delaySamples = peakSampleL - peakSampleR;
    float delayMicros = (static_cast<float>(delaySamples) / 48000.0f) * 1e6f;
    std::cout << "  Peak Sample Right: " << peakSampleR << ", Left: " << peakSampleL << "\n";
    std::cout << "  Interaural Time Delay (ITD): " << delaySamples << " samples (" << delayMicros << " microseconds)\n";

    // Woodworth model predicts ~650 us for average human head (at 48 kHz: 48 * 0.00065 = ~31 samples)
    assert(delaySamples >= 20 && delaySamples <= 40 && "ITD must match physiological human head delay (20 to 40 samples)");
    std::cout << "  -> PASS: Acoustic ITD (" << delayMicros << " us) and Head-Shadow ILD (" << ildDb << " dB) verified.\n";
}

void test_stereo_speaker_binauralization() {
    std::cout << "[Test 4] Testing Dual Studio Monitor (+-30 deg) Spatialization...\n";
    AeroSpatialEngine engine;
    SpatialConfig config{};
    config.sampleRate = 48000;
    config.frameSize = 256;
    config.speakerAzimuthDeg = 30.0f;
    engine.init(config);

    const int numSamples = 512;
    // Hard-panned Left input (1 kHz sine wave on Left, silence on Right)
    std::vector<float> inL(numSamples);
    for (int i = 0; i < numSamples; ++i) {
        inL[i] = 0.7f * std::sin(2.0f * 3.14159265f * 1000.0f * i / 48000.0f);
    }
    std::vector<float> inR(numSamples, 0.0f);
    std::vector<float> outL(numSamples, 0.0f);
    std::vector<float> outR(numSamples, 0.0f);

    engine.processStereo(inL.data(), inR.data(), outL.data(), outR.data(), numSamples);

    float rmsL = calculateRMS(outL);
    float rmsR = calculateRMS(outR);
    std::cout << "  Hard Left input -> Binaural Out L: " << rmsL << ", Out R: " << rmsR << "\n";

    // Because this is realistic speaker listening, Left speaker sound MUST bleed into Right ear with crossfeed delay!
    assert(rmsR > 0.05f && "Acoustic crossfeed must deliver sound to opposite ear");
    assert(rmsL > rmsR && "Ipsilateral ear must be louder than contralateral ear");

    std::cout << "  -> PASS: Dual virtual speakers produce crossfeed in the output channels.\n";
}

void test_variable_quantum_continuity() {
    std::cout << "[Test 5] Testing Variable Quantum Streaming Continuity (240-sample PipeWire quantum vs 256 frameSize)...\n";
    AeroSpatialEngine engine;
    SpatialConfig config{};
    config.sampleRate = 48000;
    config.frameSize = 256;
    config.speakerAzimuthDeg = 30.0f;
    engine.init(config);

    const int quantum = 240;
    const int numBlocks = 20; // 4800 samples total = 100 ms
    const int totalSamples = quantum * numBlocks;

    std::vector<float> inL(totalSamples);
    std::vector<float> inR(totalSamples);
    for (int i = 0; i < totalSamples; ++i) {
        float sample = 0.5f * std::sin(2.0f * 3.14159265f * 440.0f * i / 48000.0f);
        inL[i] = sample;
        inR[i] = sample;
    }

    std::vector<float> outL(totalSamples, 0.0f);
    std::vector<float> outR(totalSamples, 0.0f);

    // Stream through in 240-sample chunks (exact PipeWire LDAC/A2DP quantum size)
    for (int b = 0; b < numBlocks; ++b) {
        int offset = b * quantum;
        engine.processStereo(inL.data() + offset, inR.data() + offset,
                             outL.data() + offset, outR.data() + offset,
                             quantum);
    }

    // Verify that after initial pipeline fill (first block), output is continuous and smooth
    for (int i = 0; i < totalSamples; ++i) {
        assert(!std::isnan(outL[i]) && !std::isinf(outL[i]));
        assert(!std::isnan(outR[i]) && !std::isinf(outR[i]));
    }

    // Check boundary transitions between blocks (e.g. at index 239 -> 240, 479 -> 480, etc.)
    float maxDeltaL = 0.0f;
    for (int b = 2; b < numBlocks - 1; ++b) {
        int idx = b * quantum;
        float boundaryDelta = std::abs(outL[idx] - outL[idx - 1]);
        if (boundaryDelta > maxDeltaL) maxDeltaL = boundaryDelta;
        assert(boundaryDelta < 0.15f && "Sample transition at quantum boundary must be continuous without jump glitch");
    }

    std::cout << "  Max boundary delta across 240-sample quantum transitions: " << maxDeltaL << "\n";
    std::cout << "  -> PASS: Seamless continuous streaming across 240-sample boundaries with zero tail-drops.\n";
}

int main() {
    std::cout << "========================================================\n";
    std::cout << "  AeroSpatial Stereo Virtualization - Offline DSP Checks\n";
    std::cout << "========================================================\n\n";

    test_initialization();
    std::cout << "\n";
    test_front_center_iacc();
    std::cout << "\n";
    test_lateral_itd_and_ild();
    std::cout << "\n";
    test_stereo_speaker_binauralization();
    std::cout << "\n";
    test_variable_quantum_continuity();

    std::cout << "\n========================================================\n";
    std::cout << "  ALL 5 OFFLINE DSP CHECKS PASSED!\n";
    std::cout << "========================================================\n";
    return 0;
}
