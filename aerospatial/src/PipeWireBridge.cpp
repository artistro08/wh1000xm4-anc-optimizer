#include "PipeWireBridge.hpp"
#include <iostream>
#include <cstring>

namespace aerospatial {

struct PortData {
    PipeWireBridge* bridge;
};

static void on_filter_process(void* userdata, struct spa_io_position* position) {
    auto* bridge = static_cast<PipeWireBridge*>(userdata);
    bridge->processAudio(position);
}

static const struct pw_filter_events filter_events = {
    .version = PW_VERSION_FILTER_EVENTS,
    .destroy = nullptr,
    .state_changed = nullptr,
    .io_changed = nullptr,
    .param_changed = nullptr,
    .add_buffer = nullptr,
    .remove_buffer = nullptr,
    .process = on_filter_process,
    .drained = nullptr,
    .command = nullptr
};

PipeWireBridge::PipeWireBridge(AeroSpatialEngine& engine, const PipeWireConfig& config)
    : m_engine(engine), m_config(config) {}

PipeWireBridge::~PipeWireBridge() {
    stop();
}

bool PipeWireBridge::start() {
    if (m_running.load()) return true;

    int argc = 0;
    pw_init(&argc, nullptr);

    m_loop = pw_main_loop_new(nullptr);
    if (!m_loop) {
        std::cerr << "[PipeWireBridge] Failed to create PipeWire main loop\n";
        return false;
    }

    struct pw_properties* filterProps = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Filter",
        PW_KEY_MEDIA_ROLE, "DSP",
        PW_KEY_NODE_NAME, m_config.sinkName.c_str(),
        PW_KEY_NODE_DESCRIPTION, m_config.sinkDescription.c_str(),
        PW_KEY_MEDIA_CLASS, m_config.createVirtualSink ? "Audio/Sink" : "Filter/Audio",
        PW_KEY_NODE_PASSIVE, "false",
        nullptr
    );

    m_filter = pw_filter_new_simple(
        pw_main_loop_get_loop(m_loop),
        m_config.sinkName.c_str(),
        filterProps,
        &filter_events,
        this
    );

    if (!m_filter) {
        std::cerr << "[PipeWireBridge] Failed to create pw_filter\n";
        stop();
        return false;
    }

    // Input Left & Right (Sink playback ports)
    m_inPortL = pw_filter_add_port(
        m_filter,
        PW_DIRECTION_INPUT,
        PW_FILTER_PORT_FLAG_MAP_BUFFERS,
        sizeof(PortData),
        pw_properties_new(
            PW_KEY_FORMAT_DSP, "32 bit float mono audio",
            PW_KEY_PORT_NAME, "playback_FL",
            PW_KEY_AUDIO_CHANNEL, "FL",
            nullptr
        ),
        nullptr, 0
    );

    m_inPortR = pw_filter_add_port(
        m_filter,
        PW_DIRECTION_INPUT,
        PW_FILTER_PORT_FLAG_MAP_BUFFERS,
        sizeof(PortData),
        pw_properties_new(
            PW_KEY_FORMAT_DSP, "32 bit float mono audio",
            PW_KEY_PORT_NAME, "playback_FR",
            PW_KEY_AUDIO_CHANNEL, "FR",
            nullptr
        ),
        nullptr, 0
    );

    // Output Left & Right (Binaural stereo out to headphones)
    m_outPortL = pw_filter_add_port(
        m_filter,
        PW_DIRECTION_OUTPUT,
        PW_FILTER_PORT_FLAG_MAP_BUFFERS,
        sizeof(PortData),
        pw_properties_new(
            PW_KEY_FORMAT_DSP, "32 bit float mono audio",
            PW_KEY_PORT_NAME, "capture_FL",
            PW_KEY_AUDIO_CHANNEL, "FL",
            nullptr
        ),
        nullptr, 0
    );

    m_outPortR = pw_filter_add_port(
        m_filter,
        PW_DIRECTION_OUTPUT,
        PW_FILTER_PORT_FLAG_MAP_BUFFERS,
        sizeof(PortData),
        pw_properties_new(
            PW_KEY_FORMAT_DSP, "32 bit float mono audio",
            PW_KEY_PORT_NAME, "capture_FR",
            PW_KEY_AUDIO_CHANNEL, "FR",
            nullptr
        ),
        nullptr, 0
    );

    if (!m_inPortL || !m_inPortR || !m_outPortL || !m_outPortR) {
        std::cerr << "[PipeWireBridge] Failed to create all DSP ports\n";
        stop();
        return false;
    }

    // Set process latency info
    uint8_t buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
    const struct spa_pod* params[1];
    struct spa_process_latency_info latency_info = SPA_PROCESS_LATENCY_INFO_INIT(
        .ns = m_config.processLatencyMs * SPA_NSEC_PER_MSEC
    );
    params[0] = spa_process_latency_build(
        &b,
        SPA_PARAM_ProcessLatency,
        &latency_info
    );

    if (pw_filter_connect(m_filter, PW_FILTER_FLAG_RT_PROCESS, params, 1) < 0) {
        std::cerr << "[PipeWireBridge] Failed to connect pw_filter to PipeWire daemon\n";
        stop();
        return false;
    }

    m_running.store(true);
    std::cout << "[PipeWireBridge] Connected to PipeWire. Virtual Sink: \"" << m_config.sinkDescription << "\"\n";
    return true;
}

void PipeWireBridge::run() {
    if (!m_running.load() || !m_loop) return;
    pw_main_loop_run(m_loop);
}

void PipeWireBridge::stop() {
    if (!m_running.exchange(false)) return;

    if (m_loop) {
        pw_main_loop_quit(m_loop);
    }
    if (m_filter) {
        pw_filter_destroy(m_filter);
        m_filter = nullptr;
    }
    if (m_loop) {
        pw_main_loop_destroy(m_loop);
        m_loop = nullptr;
    }
    pw_deinit();
    std::cout << "[PipeWireBridge] Disconnected and destroyed.\n";
}

void PipeWireBridge::processAudio(struct spa_io_position* position) noexcept {
    if (!position) return;
    uint32_t n_samples = position->clock.duration;

    const float* inL = static_cast<const float*>(pw_filter_get_dsp_buffer(m_inPortL, n_samples));
    const float* inR = static_cast<const float*>(pw_filter_get_dsp_buffer(m_inPortR, n_samples));
    float* outL = static_cast<float*>(pw_filter_get_dsp_buffer(m_outPortL, n_samples));
    float* outR = static_cast<float*>(pw_filter_get_dsp_buffer(m_outPortR, n_samples));

    if (!outL || !outR) return;

    if (!inL || !inR) {
        std::memset(outL, 0, n_samples * sizeof(float));
        std::memset(outR, 0, n_samples * sizeof(float));
        return;
    }

    m_engine.processStereo(inL, inR, outL, outR, n_samples);
    m_processedFrames += n_samples;
}

} // namespace aerospatial
