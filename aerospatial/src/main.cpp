#include "AeroSpatialEngine.hpp"
#include "PipeWireBridge.hpp"
#include <iostream>
#include <csignal>
#include <cstdlib>

static aerospatial::PipeWireBridge* g_bridge = nullptr;

static void handle_signal(int sig) {
    std::cout << "\n[AeroSpatial] Interrupted by signal " << sig << ", stopping cleanly...\n";
    if (g_bridge) {
        g_bridge->stop();
    }
}

static void print_banner() {
    std::cout << "=================================================================\n";
    std::cout << "  AeroSpatial Stereo Virtual Speakers (Steam Audio prototype)  \n";
    std::cout << "=================================================================\n";
    std::cout << "  HRTF Standard       : Steam Audio default (optional SOFA)\n";
    std::cout << "  Input               : Stereo PCM (two virtual speakers)\n";
    std::cout << "  End-to-end playback : Not yet verified\n";
    std::cout << "  Audio Server        : Linux PipeWire 1.6+ (Native RT Filter Node)\n";
    std::cout << "=================================================================\n\n";
}

static void print_usage(const char* prog) {
    std::cout << "Usage: " << prog << " [OPTIONS]\n\n"
              << "Options:\n"
              << "  --sofa <path>       Path to custom AES69 SOFA HRTF dataset (.sofa)\n"
              << "  --azimuth <deg>     Virtual speaker angle in degrees (default: 30.0)\n"
              << "  --gain <multiplier> Speaker gain scaling factor (default: 0.85)\n"
              << "  --blend <0.0-1.0>   Spatialization blend factor (default: 1.0)\n"
              << "  --rate <hz>         Sampling rate (default: 48000)\n"
              << "  --frame <samples>   Processing frame size (default: 256)\n"
              << "  --help              Show this help message\n\n";
}

int main(int argc, char* argv[]) {
    print_banner();

    aerospatial::SpatialConfig config{};
    config.sampleRate = 48000;
    config.frameSize = 256;
    config.speakerAzimuthDeg = 30.0f;
    config.spatialBlend = 1.0f;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            print_usage(argv[0]);
            return 0;
        } else if (arg == "--sofa" && i + 1 < argc) {
            config.sofaPath = argv[++i];
        } else if (arg == "--azimuth" && i + 1 < argc) {
            config.speakerAzimuthDeg = std::stof(argv[++i]);
        } else if (arg == "--gain" && i + 1 < argc) {
            config.speakerGain = std::stof(argv[++i]);
        } else if (arg == "--blend" && i + 1 < argc) {
            config.spatialBlend = std::stof(argv[++i]);
        } else if (arg == "--rate" && i + 1 < argc) {
            config.sampleRate = std::stoul(argv[++i]);
        } else if (arg == "--frame" && i + 1 < argc) {
            config.frameSize = std::stoul(argv[++i]);
        }
    }

    std::cout << "[AeroSpatial] Initializing DSP Engine...\n";
    std::cout << "  - Sample Rate : " << config.sampleRate << " Hz\n";
    std::cout << "  - Frame Size  : " << config.frameSize << " samples\n";
    std::cout << "  - Speaker Pos : +-" << config.speakerAzimuthDeg << " degrees\n";
    std::cout << "  - Speaker Gain: " << config.speakerGain << "x\n";
    std::cout << "  - HRTF Model  : " << (config.sofaPath.empty() ? "Steam Audio default HRTF" : config.sofaPath) << "\n";

    aerospatial::AeroSpatialEngine engine;
    if (!engine.init(config)) {
        std::cerr << "[AeroSpatial] ERROR: Failed to initialize Steam Audio core.\n";
        return 1;
    }

    aerospatial::PipeWireConfig pwConfig{};
    pwConfig.sinkName = "aerospatial_3d_sink";
    pwConfig.sinkDescription = "AeroSpatial Stereo Virtual Speakers (prototype)";
    pwConfig.processLatencyMs = 2;

    aerospatial::PipeWireBridge bridge(engine, pwConfig);
    g_bridge = &bridge;

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    if (!bridge.start()) {
        std::cerr << "[AeroSpatial] ERROR: Could not connect to PipeWire server.\n";
        return 1;
    }

    std::cout << "\n[AeroSpatial] PipeWire stereo virtualization node registered.\n";
    std::cout << "  -> Open pavucontrol or GNOME/KDE Sound Settings and select:\n";
    std::cout << "     \"AeroSpatial Stereo Virtual Speakers (prototype)\" as your audio output.\n";
    std::cout << "  -> Check the graph connection and audio output before use.\n";
    std::cout << "  -> Press Ctrl+C to exit.\n\n";

    bridge.run();

    std::cout << "[AeroSpatial] Teardown complete. Exiting.\n";
    return 0;
}
