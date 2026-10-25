"""Safety, Checksum, Dry-Run, Staged Injection, Verification, and Rollback Engine."""

from xm4_optimizer.safety.checksum import (
    compute_bkdr_hash,
    compute_nvdm_checksum,
    compute_race_fota_crc8,
    verify_nvdm_checksum,
    verify_race_fota_crc8,
)
from xm4_optimizer.safety.dry_run import (
    DryRunReport,
    DryRunWriteViolationError,
    ReadOnlyTransportGuard,
    StageCoefficientDiff,
    execute_dry_run,
    generate_dry_run_report,
)
from xm4_optimizer.safety.injector import (
    InjectionError,
    InjectionResult,
    UnstableFilterHoldError,
    execute_injection,
    update_nvdm_image_item,
)
from xm4_optimizer.safety.rollback import (
    BackupMetadata,
    RollbackError,
    create_nvdm_backup,
    execute_rollback,
)
from xm4_optimizer.safety.verifier import (
    VerificationError,
    verify_dsp_registers_atci,
    verify_nvdm_block_in_flash,
    verify_readback,
)

__all__ = [
    "BackupMetadata",
    "DryRunReport",
    "DryRunWriteViolationError",
    "InjectionError",
    "InjectionResult",
    "ReadOnlyTransportGuard",
    "RollbackError",
    "StageCoefficientDiff",
    "UnstableFilterHoldError",
    "VerificationError",
    "compute_bkdr_hash",
    "compute_nvdm_checksum",
    "compute_race_fota_crc8",
    "create_nvdm_backup",
    "execute_dry_run",
    "execute_injection",
    "execute_rollback",
    "generate_dry_run_report",
    "update_nvdm_image_item",
    "verify_dsp_registers_atci",
    "verify_nvdm_block_in_flash",
    "verify_nvdm_checksum",
    "verify_race_fota_crc8",
    "verify_readback",
]
