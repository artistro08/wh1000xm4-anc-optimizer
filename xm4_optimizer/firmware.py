"""Known official Sony firmware images, identified by SHA-256.

Hashes were computed from the files in the community archive lzghzr/MDR_Proxy
(firmware/2943/MDRID294300/, commit 2e80d32), which mirrors Sony's update server.
MDRID294300 is the WH-1000XM4. The flasher refuses files that are not listed here
unless the user explicitly overrides the check.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class KnownFirmware:
    model_id: str
    model: str
    version: str
    size: int


KNOWN_FIRMWARE: Dict[str, KnownFirmware] = {
    "ee2a4b60c5f322d56fd9e6ea51778cbbc847f816781c385f40d45959555ac508": KnownFirmware("MDRID294300", "WH-1000XM4", "2.5.0", 1395504),
    "3dc86be5cbc5c3f084f14be6ce12b37cc22aab5d0634f83becba4ee6217f1ead": KnownFirmware("MDRID294300", "WH-1000XM4", "2.6.0", 4150976),
    "4564973e2d57cf22528dbb1c5efbf9a760ecddad62b4386c3ada6b22af80b23f": KnownFirmware("MDRID294300", "WH-1000XM4", "2.7.1", 4151664),
    "589e06b4fffbc74114f495ca0424761c0e3784b06adbf56d3f431559efe45b38": KnownFirmware("MDRID294300", "WH-1000XM4", "2.9.0", 4151776),
    "2c82f6e5467722d2ea990805b001694704eea4b54ee36e8d38200b761b5c6f75": KnownFirmware("MDRID294300", "WH-1000XM4", "3.0.1", 4151872),
}


def identify_firmware(data: bytes) -> Optional[KnownFirmware]:
    """Returns the matching known image, or None if the hash or size is not listed."""
    known = KNOWN_FIRMWARE.get(hashlib.sha256(data).hexdigest())
    if known is None or known.size != len(data):
        return None
    return known


def identify_firmware_file(path: Path) -> Optional[KnownFirmware]:
    return identify_firmware(Path(path).read_bytes())
