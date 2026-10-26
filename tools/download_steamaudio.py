#!/usr/bin/env python3
"""
Downloads and unpacks the Valve Steam Audio v4.8.1 SDK for Linux x86_64.
Places headers and libphonon.so in third_party/steamaudio.
"""

import os
import sys
import urllib.request
import zipfile
import shutil

SDK_VERSION = "4.8.1"
URL = f"https://github.com/ValveSoftware/steam-audio/releases/download/v{SDK_VERSION}/steamaudio_{SDK_VERSION}.zip"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
TARGET_DIR = os.path.join(REPO_ROOT, "third_party", "steamaudio")
TEMP_ZIP = "/tmp/steamaudio_sdk.zip"
TEMP_EXTRACT = "/tmp/steamaudio_extracted"

def main():
    print(f"[*] Valve Steam Audio SDK v{SDK_VERSION} Setup Tool")
    lib_path = os.path.join(TARGET_DIR, "lib", "linux-x64", "libphonon.so")
    if os.path.exists(lib_path):
        print(f"[+] Steam Audio library already present at: {lib_path}")
        return

    print(f"[*] Downloading Steam Audio v{SDK_VERSION} from GitHub releases...")
    if not os.path.exists(TEMP_ZIP):
        urllib.request.urlretrieve(URL, TEMP_ZIP)
        print("[+] Download complete.")

    print("[*] Extracting SDK package...")
    if os.path.exists(TEMP_EXTRACT):
        shutil.rmtree(TEMP_EXTRACT)
    with zipfile.ZipFile(TEMP_ZIP, "r") as z:
        z.extractall(TEMP_EXTRACT)

    sdk_root = os.path.join(TEMP_EXTRACT, "steamaudio")
    os.makedirs(os.path.join(TARGET_DIR, "include"), exist_ok=True)
    os.makedirs(os.path.join(TARGET_DIR, "lib", "linux-x64"), exist_ok=True)

    print("[*] Copying C headers and shared libraries...")
    for h in ["phonon.h", "phonon_interfaces.h", "phonon_version.h"]:
        src = os.path.join(sdk_root, "include", h)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(TARGET_DIR, "include", h))

    src_lib = os.path.join(sdk_root, "lib", "linux-x64", "libphonon.so")
    if os.path.exists(src_lib):
        shutil.copy(src_lib, os.path.join(TARGET_DIR, "lib", "linux-x64", "libphonon.so"))

    print(f"[+] Valve Steam Audio SDK v{SDK_VERSION} ready in: {TARGET_DIR}")

if __name__ == "__main__":
    main()
