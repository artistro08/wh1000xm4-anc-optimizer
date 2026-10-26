// Inspect the same system SOFA HRTF used by stereo-virtual-speakers.conf.
// Build: c++ -std=c++17 probe_sofa_hrtf.cpp $(pkg-config --cflags --libs libmysofa) -o /tmp/probe_sofa_hrtf
#include <mysofa.h>

#include <cmath>
#include <iostream>
#include <vector>

int main(int argc, char** argv) {
    const char* path = argc > 1 ? argv[1] : "/usr/share/libmysofa/MIT_KEMAR_normal_pinna.sofa";
    int length = 0;
    int error = 0;
    MYSOFA_EASY* hrtf = mysofa_open(path, 48000.0f, &length, &error);
    if (!hrtf || length <= 0) {
        std::cerr << "SOFA load failed: error=" << error << '\n';
        return 1;
    }

    std::cout << "SOFA filter length: " << length << " samples\n";
    for (float azimuth : {30.0f, 330.0f}) {
        float position[3] = {azimuth, 0.0f, 1.0f};
        mysofa_s2c(position);
        std::vector<float> left(length), right(length);
        float delay_left = 0.0f, delay_right = 0.0f;
        mysofa_getfilter_float(hrtf, position[0], position[1], position[2],
                              left.data(), right.data(), &delay_left, &delay_right);
        double energy_left = 0.0, energy_right = 0.0;
        for (int i = 0; i < length; ++i) {
            energy_left += left[i] * left[i];
            energy_right += right[i] * right[i];
        }
        std::cout << "azimuth=" << azimuth << " deg"
                  << " energy_L=" << energy_left << " energy_R=" << energy_right
                  << " delay_L=" << delay_left << " s delay_R=" << delay_right << " s\n";
        if (!std::isfinite(energy_left) || !std::isfinite(energy_right) ||
            energy_left <= 0.0 || energy_right <= 0.0) {
            mysofa_close(hrtf);
            return 2;
        }
    }
    mysofa_close(hrtf);
}
