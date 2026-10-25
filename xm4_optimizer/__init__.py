"""Sony WH-1000XM4 ANC Optimizer package.

A modular optimization suite providing NVDM parameter extraction,
biquad IIR DSP coefficient synthesis, dry-run simulation, and safe injection
for Sony WH-1000XM4 active noise cancellation.
"""

from xm4_optimizer.dsp import (
    BiquadCoefficients,
    SynthesisResult,
    synthesize_anc_filter,
    verify_nyquist_stability,
)
from xm4_optimizer.nvdm import (
    CXD90050AlignmentBlock,
    NvdmPartition,
    extract_cxd90050_blocks,
    parse_nvdm_image,
)
from xm4_optimizer.safety import (
    execute_dry_run,
    execute_injection,
    execute_rollback,
    verify_readback,
)
from xm4_optimizer.transport import (
    BluetoothRfcommTransport,
    DeviceTransport,
    VirtualXM4Device,
)

__version__ = "0.1.0"

__all__ = [
    "BiquadCoefficients",
    "BluetoothRfcommTransport",
    "CXD90050AlignmentBlock",
    "DeviceTransport",
    "NvdmPartition",
    "SynthesisResult",
    "VirtualXM4Device",
    "__version__",
    "execute_dry_run",
    "execute_injection",
    "execute_rollback",
    "extract_cxd90050_blocks",
    "parse_nvdm_image",
    "synthesize_anc_filter",
    "verify_nyquist_stability",
    "verify_readback",
]
