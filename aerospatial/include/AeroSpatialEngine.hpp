#pragma once

#include <phonon.h>
#include <string>
#include <vector>
#include <cstdint>
#include <cmath>
#include <stdexcept>

namespace aerospatial {

enum class RenderMode {
    StereoVirtualMonitors, // Emulates dual studio monitors at +-30 deg in front
    Orbital360,           // Single source rotating around the head in 3D
    BinauralPassthrough   // Pure bypass for calibration/A-B testing
};

struct SpatialConfig {
    uint32_t sampleRate = 48000;
    uint32_t frameSize = 256;
    RenderMode mode = RenderMode::StereoVirtualMonitors;
    std::string sofaPath = ""; // Empty string for built-in KEMAR HRTF
    float speakerAzimuthDeg = 30.0f; // +-30 degrees for stereo
    float sourceAzimuthDeg = 0.0f;
    float sourceElevationDeg = 0.0f;
    float spatialBlend = 1.0f; // 1.0 = fully spatialized, 0.0 = raw input
    bool enableEarlyReflections = true;
    float speakerGain = 0.85f; // Master speaker gain multiplier (0.85 for unity listening loudness)
};

class AeroSpatialEngine {
public:
    AeroSpatialEngine();
    ~AeroSpatialEngine();

    // Non-copyable, movable
    AeroSpatialEngine(const AeroSpatialEngine&) = delete;
    AeroSpatialEngine& operator=(const AeroSpatialEngine&) = delete;
    AeroSpatialEngine(AeroSpatialEngine&&) noexcept;
    AeroSpatialEngine& operator=(AeroSpatialEngine&&) noexcept;

    // Initialize the engine with config
    bool init(const SpatialConfig& config);
    void shutdown();

    // Dynamic parameter updates (RT-safe)
    void setOrbitalCoordinates(float azimuthDeg, float elevationDeg) noexcept;
    void setSpatialBlend(float blend) noexcept;
    void setSpeakerGain(float gain) noexcept { m_config.speakerGain = std::max(0.0f, gain); }

    // RT-safe Audio Processing
    // Processes interleaved or planar stereo audio buffers
    void processStereo(const float* inL, const float* inR, 
                       float* outL, float* outR, 
                       uint32_t numSamples) noexcept;

    // Process a single point source to binaural stereo
    void processPointSource(const float* inMono, 
                            float* outL, float* outR, 
                            uint32_t numSamples, 
                            float azimuthDeg, float elevationDeg) noexcept;

    // Resets internal FIFO and filter history
    void reset() noexcept;

    [[nodiscard]] bool isInitialized() const noexcept { return m_initialized; }
    [[nodiscard]] const SpatialConfig& getConfig() const noexcept { return m_config; }

    // Helper: spherical to right-handed Cartesian vector
    static IPLVector3 sphericalToVector(float azimuthDeg, float elevationDeg) noexcept;

private:
    void allocateBuffers();
    void freeBuffers();

    struct AudioRingBuffer {
        std::vector<float> buffer;
        size_t writePos = 0;
        size_t readPos = 0;
        size_t count = 0;

        void init(size_t capacity) {
            buffer.assign(capacity, 0.0f);
            writePos = 0;
            readPos = 0;
            count = 0;
        }

        void reset() {
            writePos = 0;
            readPos = 0;
            count = 0;
            std::fill(buffer.begin(), buffer.end(), 0.0f);
        }

        [[nodiscard]] size_t availableRead() const noexcept { return count; }
        [[nodiscard]] size_t availableWrite() const noexcept { return buffer.size() - count; }

        void write(const float* data, size_t n) noexcept {
            size_t cap = buffer.size();
            size_t toWrite = std::min(n, availableWrite());
            for (size_t i = 0; i < toWrite; ++i) {
                buffer[writePos] = data[i];
                writePos = (writePos + 1 == cap) ? 0 : writePos + 1;
            }
            count += toWrite;
        }

        void read(float* data, size_t n) noexcept {
            size_t cap = buffer.size();
            size_t toRead = std::min(n, count);
            for (size_t i = 0; i < toRead; ++i) {
                data[i] = buffer[readPos];
                readPos = (readPos + 1 == cap) ? 0 : readPos + 1;
            }
            count -= toRead;
        }

        void readDiscard(size_t n) noexcept {
            size_t toDiscard = std::min(n, count);
            size_t cap = buffer.size();
            readPos = (readPos + toDiscard) % cap;
            count -= toDiscard;
        }
    };

    SpatialConfig m_config;
    bool m_initialized = false;

    IPLContext m_context = nullptr;
    IPLHRTF m_hrtf = nullptr;

    // Separate binaural effects for Left and Right virtual monitors
    IPLBinauralEffect m_effectL = nullptr;
    IPLBinauralEffect m_effectR = nullptr;

    // Intermediate planar buffers for RT-safe processing
    IPLAudioBuffer m_inBufMonoL{};
    IPLAudioBuffer m_inBufMonoR{};
    IPLAudioBuffer m_outBufBinauralL{};
    IPLAudioBuffer m_outBufBinauralR{};

    // Internal FIFO block adapter buffers for quantum and variable block streaming
    AudioRingBuffer m_inFifoL;
    AudioRingBuffer m_inFifoR;
    AudioRingBuffer m_outFifoL;
    AudioRingBuffer m_outFifoR;

    // Dynamic RT parameters
    float m_orbitalAzimuthDeg = 0.0f;
    float m_orbitalElevationDeg = 0.0f;
    float m_spatialBlend = 1.0f;
};

} // namespace aerospatial
