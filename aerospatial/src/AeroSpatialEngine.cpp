#include "AeroSpatialEngine.hpp"
#include <cstring>
#include <iostream>
#include <algorithm>

namespace aerospatial {

AeroSpatialEngine::AeroSpatialEngine() = default;

AeroSpatialEngine::~AeroSpatialEngine() {
    shutdown();
}

AeroSpatialEngine::AeroSpatialEngine(AeroSpatialEngine&& other) noexcept {
    *this = std::move(other);
}

AeroSpatialEngine& AeroSpatialEngine::operator=(AeroSpatialEngine&& other) noexcept {
    if (this != &other) {
        shutdown();

        m_config = other.m_config;
        m_initialized = other.m_initialized;
        m_context = other.m_context;
        m_hrtf = other.m_hrtf;
        m_effectL = other.m_effectL;
        m_effectR = other.m_effectR;
        m_inBufMonoL = other.m_inBufMonoL;
        m_inBufMonoR = other.m_inBufMonoR;
        m_outBufBinauralL = other.m_outBufBinauralL;
        m_outBufBinauralR = other.m_outBufBinauralR;
        m_orbitalAzimuthDeg = other.m_orbitalAzimuthDeg;
        m_orbitalElevationDeg = other.m_orbitalElevationDeg;
        m_spatialBlend = other.m_spatialBlend;

        m_inFifoL = std::move(other.m_inFifoL);
        m_inFifoR = std::move(other.m_inFifoR);
        m_outFifoL = std::move(other.m_outFifoL);
        m_outFifoR = std::move(other.m_outFifoR);

        other.m_initialized = false;
        other.m_context = nullptr;
        other.m_hrtf = nullptr;
        other.m_effectL = nullptr;
        other.m_effectR = nullptr;
    }
    return *this;
}

IPLVector3 AeroSpatialEngine::sphericalToVector(float azimuthDeg, float elevationDeg) noexcept {
    constexpr float kDegToRad = 3.14159265358979323846f / 180.0f;
    float azRad = azimuthDeg * kDegToRad;
    float elRad = elevationDeg * kDegToRad;

    // Steam Audio Right-Handed Coordinate System:
    // +X: Right, +Y: Up, -Z: Ahead
    return IPLVector3{
        std::sin(azRad) * std::cos(elRad),   // Right (+X)
        std::sin(elRad),                      // Up (+Y)
        -std::cos(azRad) * std::cos(elRad)   // Ahead (-Z)
    };
}

bool AeroSpatialEngine::init(const SpatialConfig& config) {
    shutdown();
    m_config = config;

    IPLContextSettings contextSettings{};
    contextSettings.version = STEAMAUDIO_VERSION;

    IPLerror err = iplContextCreate(&contextSettings, &m_context);
    if (err != IPL_STATUS_SUCCESS) {
        std::cerr << "[AeroSpatial] Failed to create Steam Audio context. Error: " << err << "\n";
        return false;
    }

    IPLAudioSettings audioSettings{};
    audioSettings.samplingRate = static_cast<IPLint32>(m_config.sampleRate);
    audioSettings.frameSize = static_cast<IPLint32>(m_config.frameSize);

    IPLHRTFSettings hrtfSettings{};
    hrtfSettings.volume = 1.0f;
    hrtfSettings.normType = IPL_HRTFNORMTYPE_RMS;

    if (m_config.sofaPath.empty()) {
        hrtfSettings.type = IPL_HRTFTYPE_DEFAULT;
    } else {
        hrtfSettings.type = IPL_HRTFTYPE_SOFA;
        hrtfSettings.sofaFileName = m_config.sofaPath.c_str();
    }

    err = iplHRTFCreate(m_context, &audioSettings, &hrtfSettings, &m_hrtf);
    if (err != IPL_STATUS_SUCCESS) {
        std::cerr << "[AeroSpatial] Failed to load HRTF (SOFA/Default). Error: " << err << "\n";
        shutdown();
        return false;
    }

    IPLBinauralEffectSettings effectSettings{};
    effectSettings.hrtf = m_hrtf;

    err = iplBinauralEffectCreate(m_context, &audioSettings, &effectSettings, &m_effectL);
    if (err != IPL_STATUS_SUCCESS) {
        std::cerr << "[AeroSpatial] Failed to create Left Binaural Effect. Error: " << err << "\n";
        shutdown();
        return false;
    }

    err = iplBinauralEffectCreate(m_context, &audioSettings, &effectSettings, &m_effectR);
    if (err != IPL_STATUS_SUCCESS) {
        std::cerr << "[AeroSpatial] Failed to create Right Binaural Effect. Error: " << err << "\n";
        shutdown();
        return false;
    }

    allocateBuffers();
    m_spatialBlend = m_config.spatialBlend;
    m_orbitalAzimuthDeg = m_config.sourceAzimuthDeg;
    m_orbitalElevationDeg = m_config.sourceElevationDeg;
    m_initialized = true;

    return true;
}

void AeroSpatialEngine::shutdown() {
    if (!m_initialized && !m_context) return;

    freeBuffers();

    if (m_effectL) {
        iplBinauralEffectRelease(&m_effectL);
        m_effectL = nullptr;
    }
    if (m_effectR) {
        iplBinauralEffectRelease(&m_effectR);
        m_effectR = nullptr;
    }
    if (m_hrtf) {
        iplHRTFRelease(&m_hrtf);
        m_hrtf = nullptr;
    }
    if (m_context) {
        iplContextRelease(&m_context);
        m_context = nullptr;
    }

    m_initialized = false;
}

void AeroSpatialEngine::allocateBuffers() {
    iplAudioBufferAllocate(m_context, 1, static_cast<IPLint32>(m_config.frameSize), &m_inBufMonoL);
    iplAudioBufferAllocate(m_context, 1, static_cast<IPLint32>(m_config.frameSize), &m_inBufMonoR);
    iplAudioBufferAllocate(m_context, 2, static_cast<IPLint32>(m_config.frameSize), &m_outBufBinauralL);
    iplAudioBufferAllocate(m_context, 2, static_cast<IPLint32>(m_config.frameSize), &m_outBufBinauralR);

    // Preallocate ring buffer capacity (RT-safe, no heap allocations in processing thread)
    size_t fifoCap = std::max<size_t>(16384, m_config.frameSize * 16);
    m_inFifoL.init(fifoCap);
    m_inFifoR.init(fifoCap);
    m_outFifoL.init(fifoCap);
    m_outFifoR.init(fifoCap);
}

void AeroSpatialEngine::freeBuffers() {
    if (!m_context) return;
    iplAudioBufferFree(m_context, &m_inBufMonoL);
    iplAudioBufferFree(m_context, &m_inBufMonoR);
    iplAudioBufferFree(m_context, &m_outBufBinauralL);
    iplAudioBufferFree(m_context, &m_outBufBinauralR);

    m_inFifoL.reset();
    m_inFifoR.reset();
    m_outFifoL.reset();
    m_outFifoR.reset();
}

void AeroSpatialEngine::reset() noexcept {
    m_inFifoL.reset();
    m_inFifoR.reset();
    m_outFifoL.reset();
    m_outFifoR.reset();
    if (m_effectL) iplBinauralEffectReset(m_effectL);
    if (m_effectR) iplBinauralEffectReset(m_effectR);
}

void AeroSpatialEngine::setOrbitalCoordinates(float azimuthDeg, float elevationDeg) noexcept {
    m_orbitalAzimuthDeg = azimuthDeg;
    m_orbitalElevationDeg = elevationDeg;
}

void AeroSpatialEngine::setSpatialBlend(float blend) noexcept {
    m_spatialBlend = std::clamp(blend, 0.0f, 1.0f);
}

void AeroSpatialEngine::processStereo(const float* inL, const float* inR,
                                     float* outL, float* outR,
                                     uint32_t numSamples) noexcept {
    if (!m_initialized) {
        if (inL && outL) std::memcpy(outL, inL, numSamples * sizeof(float));
        if (inR && outR) std::memcpy(outR, inR, numSamples * sizeof(float));
        return;
    }

    if (inL) {
        m_inFifoL.write(inL, numSamples);
    } else {
        float zero = 0.0f;
        for (uint32_t i = 0; i < numSamples; ++i) m_inFifoL.write(&zero, 1);
    }

    if (inR) {
        m_inFifoR.write(inR, numSamples);
    } else {
        float zero = 0.0f;
        for (uint32_t i = 0; i < numSamples; ++i) m_inFifoR.write(&zero, 1);
    }

    // Process all full frameSize blocks without zero padding or discarded samples
    while (m_inFifoL.availableRead() >= m_config.frameSize && m_inFifoR.availableRead() >= m_config.frameSize) {
        m_inFifoL.read(m_inBufMonoL.data[0], m_config.frameSize);
        m_inFifoR.read(m_inBufMonoR.data[0], m_config.frameSize);

        // 1. Spatialize Left Monitor (at -speakerAzimuthDeg)
        IPLBinauralEffectParams paramsL{};
        paramsL.direction = sphericalToVector(-m_config.speakerAzimuthDeg, 0.0f);
        paramsL.interpolation = IPL_HRTFINTERPOLATION_BILINEAR;
        paramsL.spatialBlend = m_spatialBlend;
        paramsL.hrtf = m_hrtf;
        iplBinauralEffectApply(m_effectL, &paramsL, &m_inBufMonoL, &m_outBufBinauralL);

        // 2. Spatialize Right Monitor (at +speakerAzimuthDeg)
        IPLBinauralEffectParams paramsR{};
        paramsR.direction = sphericalToVector(+m_config.speakerAzimuthDeg, 0.0f);
        paramsR.interpolation = IPL_HRTFINTERPOLATION_BILINEAR;
        paramsR.spatialBlend = m_spatialBlend;
        paramsR.hrtf = m_hrtf;
        iplBinauralEffectApply(m_effectR, &paramsR, &m_inBufMonoR, &m_outBufBinauralR);

        // 3. Sum Left and Right binaural pairs with calibrated speaker gain and safety clamping
        const float kSpeakerGain = m_config.speakerGain;
        const float* outLL = m_outBufBinauralL.data[0];
        const float* outLR = m_outBufBinauralL.data[1];
        const float* outRL = m_outBufBinauralR.data[0];
        const float* outRR = m_outBufBinauralR.data[1];

        for (uint32_t i = 0; i < m_config.frameSize; ++i) {
            float sL = std::clamp((outLL[i] + outRL[i]) * kSpeakerGain, -1.0f, 1.0f);
            float sR = std::clamp((outLR[i] + outRR[i]) * kSpeakerGain, -1.0f, 1.0f);
            m_outFifoL.write(&sL, 1);
            m_outFifoR.write(&sR, 1);
        }
    }

    // Read available samples to output buffer
    size_t avail = std::min(m_outFifoL.availableRead(), m_outFifoR.availableRead());
    size_t toRead = std::min(static_cast<size_t>(numSamples), avail);
    if (toRead > 0) {
        if (outL) m_outFifoL.read(outL, toRead);
        else m_outFifoL.readDiscard(toRead);

        if (outR) m_outFifoR.read(outR, toRead);
        else m_outFifoR.readDiscard(toRead);
    }
    if (toRead < numSamples) {
        if (outL) std::memset(outL + toRead, 0, (numSamples - toRead) * sizeof(float));
        if (outR) std::memset(outR + toRead, 0, (numSamples - toRead) * sizeof(float));
    }
}

void AeroSpatialEngine::processPointSource(const float* inMono,
                                          float* outL, float* outR,
                                          uint32_t numSamples,
                                          float azimuthDeg, float elevationDeg) noexcept {
    if (!m_initialized) {
        if (inMono && outL) std::memcpy(outL, inMono, numSamples * sizeof(float));
        if (inMono && outR) std::memcpy(outR, inMono, numSamples * sizeof(float));
        return;
    }

    if (inMono) {
        m_inFifoL.write(inMono, numSamples);
    } else {
        float zero = 0.0f;
        for (uint32_t i = 0; i < numSamples; ++i) m_inFifoL.write(&zero, 1);
    }

    while (m_inFifoL.availableRead() >= m_config.frameSize) {
        m_inFifoL.read(m_inBufMonoL.data[0], m_config.frameSize);

        IPLBinauralEffectParams params{};
        params.direction = sphericalToVector(azimuthDeg, elevationDeg);
        params.interpolation = IPL_HRTFINTERPOLATION_BILINEAR;
        params.spatialBlend = m_spatialBlend;
        params.hrtf = m_hrtf;
        iplBinauralEffectApply(m_effectL, &params, &m_inBufMonoL, &m_outBufBinauralL);

        m_outFifoL.write(m_outBufBinauralL.data[0], m_config.frameSize);
        m_outFifoR.write(m_outBufBinauralL.data[1], m_config.frameSize);
    }

    size_t avail = std::min(m_outFifoL.availableRead(), m_outFifoR.availableRead());
    size_t toRead = std::min(static_cast<size_t>(numSamples), avail);
    if (toRead > 0) {
        if (outL) m_outFifoL.read(outL, toRead);
        else m_outFifoL.readDiscard(toRead);

        if (outR) m_outFifoR.read(outR, toRead);
        else m_outFifoR.readDiscard(toRead);
    }
    if (toRead < numSamples) {
        if (outL) std::memset(outL + toRead, 0, (numSamples - toRead) * sizeof(float));
        if (outR) std::memset(outR + toRead, 0, (numSamples - toRead) * sizeof(float));
    }
}

} // namespace aerospatial
