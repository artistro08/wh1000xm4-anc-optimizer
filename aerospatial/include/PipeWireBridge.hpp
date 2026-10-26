#pragma once

#include "AeroSpatialEngine.hpp"
#include <pipewire/pipewire.h>
#include <pipewire/filter.h>
#include <spa/param/latency-utils.h>
#include <spa/pod/builder.h>
#include <atomic>
#include <memory>
#include <string>

namespace aerospatial {

struct PipeWireConfig {
    std::string sinkName = "aerospatial_3d_sink";
    std::string sinkDescription = "AeroSpatial Stereo Virtual Speakers (prototype)";
    uint32_t processLatencyMs = 2; // 2 ms deterministic latency
    bool createVirtualSink = true;
};

class PipeWireBridge {
public:
    PipeWireBridge(AeroSpatialEngine& engine, const PipeWireConfig& config = {});
    ~PipeWireBridge();

    PipeWireBridge(const PipeWireBridge&) = delete;
    PipeWireBridge& operator=(const PipeWireBridge&) = delete;

    // Connects to PipeWire server and registers ports
    bool start();

    // Runs the main loop (blocking)
    void run();

    // Stops the main loop and disconnects
    void stop();

    // Check if running
    [[nodiscard]] bool isRunning() const noexcept { return m_running.load(); }

    // Audio callback from PipeWire RT thread
    void processAudio(struct spa_io_position* position) noexcept;

private:
    AeroSpatialEngine& m_engine;
    PipeWireConfig m_config;

    struct pw_main_loop* m_loop = nullptr;
    struct pw_filter* m_filter = nullptr;

    void* m_inPortL = nullptr;
    void* m_inPortR = nullptr;
    void* m_outPortL = nullptr;
    void* m_outPortR = nullptr;

    std::atomic<bool> m_running{false};
    uint64_t m_processedFrames = 0;
};

} // namespace aerospatial
