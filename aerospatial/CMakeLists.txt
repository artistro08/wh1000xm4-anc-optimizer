cmake_minimum_required(VERSION 3.20)
project(AeroSpatial VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Find PipeWire & SPA
find_package(PkgConfig REQUIRED)
pkg_check_modules(PIPEWIRE REQUIRED libpipewire-0.3)
pkg_check_modules(LIBSPA REQUIRED libspa-0.2)

# Steam Audio Paths
set(STEAMAUDIO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../third_party/steamaudio")
set(STEAMAUDIO_INCLUDE_DIR "${STEAMAUDIO_ROOT}/include")
set(STEAMAUDIO_LIB_DIR "${STEAMAUDIO_ROOT}/lib/linux-x64")

include_directories(
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${STEAMAUDIO_INCLUDE_DIR}
    ${PIPEWIRE_INCLUDE_DIRS}
    ${LIBSPA_INCLUDE_DIRS}
)

link_directories(
    ${STEAMAUDIO_LIB_DIR}
    ${PIPEWIRE_LIBRARY_DIRS}
)

# Core AeroSpatial Engine library
add_library(aerospatial_core STATIC
    src/AeroSpatialEngine.cpp
    src/PipeWireBridge.cpp
)
target_link_libraries(aerospatial_core
    PUBLIC
    phonon
    ${PIPEWIRE_LIBRARIES}
)
set_target_properties(aerospatial_core PROPERTIES
    BUILD_RPATH "${STEAMAUDIO_LIB_DIR}"
    INSTALL_RPATH "${STEAMAUDIO_LIB_DIR}"
)

# Test executable
add_executable(test_aerospatial
    tests/test_aerospatial.cpp
)
target_link_libraries(test_aerospatial
    PRIVATE
    aerospatial_core
    phonon
    m
)
set_target_properties(test_aerospatial PROPERTIES
    BUILD_RPATH "${STEAMAUDIO_LIB_DIR}"
    INSTALL_RPATH "${STEAMAUDIO_LIB_DIR}"
)

# Standalone PipeWire 3D Audio Daemon
add_executable(aerospatial-daemon
    src/main.cpp
)
target_link_libraries(aerospatial-daemon
    PRIVATE
    aerospatial_core
    phonon
    ${PIPEWIRE_LIBRARIES}
    m
)
set_target_properties(aerospatial-daemon PROPERTIES
    BUILD_RPATH "${STEAMAUDIO_LIB_DIR}"
    INSTALL_RPATH "${STEAMAUDIO_LIB_DIR}"
)
