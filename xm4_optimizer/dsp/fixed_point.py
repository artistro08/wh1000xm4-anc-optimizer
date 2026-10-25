"""Fixed-point numerical conversions and binary packing for Sony CXD90050 DSP.

Provides Q28 (4.28) and Q31 (1.31) fixed-point conversions, saturation clamping,
and struct packing/unpacking routines (<5i) for biquad filter coefficients.
"""

from __future__ import annotations

import math
import struct
from typing import Sequence, Tuple

# Constants
INT32_MIN: int = -(1 << 31)  # -2147483648
INT32_MAX: int = (1 << 31) - 1  # 2147483647

# Q28 Constants (4.28 signed fixed-point)
Q28_FRAC_BITS: int = 28
Q28_SCALE: float = float(1 << 28)  # 268435456.0
Q28_MIN_FLOAT: float = -8.0  # -2147483648 / 268435456
Q28_MAX_FLOAT: float = float(INT32_MAX) / Q28_SCALE  # 7.99999999627471
Q28_RESOLUTION: float = 1.0 / Q28_SCALE  # ~3.725290298461914e-09

# Q31 Constants (1.31 signed fractional fixed-point)
Q31_FRAC_BITS: int = 31
Q31_SCALE: float = float(1 << 31)  # 2147483648.0
Q31_MIN_FLOAT: float = -1.0  # -2147483648 / 2147483648
Q31_MAX_FLOAT: float = float(INT32_MAX) / Q31_SCALE  # 0.9999999995343387
Q31_RESOLUTION: float = 1.0 / Q31_SCALE  # ~4.656612873077393e-10

# Binary packing format
BIQUAD_STRUCT_FORMAT: str = "<5i"  # 5 little-endian 32-bit signed integers
BIQUAD_BYTE_SIZE: int = 20  # 5 * 4 = 20 bytes


def clamp_int32(val: int) -> int:
    """Clamps an integer to signed 32-bit range [-2147483648, 2147483647]."""
    if val < INT32_MIN:
        return INT32_MIN
    if val > INT32_MAX:
        return INT32_MAX
    return val


def float_to_q28(val: float, clamp: bool = True) -> int:
    """Converts a float to a 32-bit signed Q28 fixed-point integer (4.28).

    Args:
        val: Floating point value to convert.
        clamp: If True, saturates values outside [-8.0, 7.999999996] to INT32_MIN / INT32_MAX.
               If False, raises OverflowError on out-of-range values.

    Returns:
        Signed 32-bit integer representing the Q28 value.

    Raises:
        ValueError: If val is NaN or infinite.
        OverflowError: If clamp is False and val overflows the Q28 range.
    """
    if math.isnan(val) or math.isinf(val):
        raise ValueError(f"Cannot convert non-finite float {val} to Q28")

    if clamp:
        if val >= Q28_MAX_FLOAT:
            return INT32_MAX
        if val <= Q28_MIN_FLOAT:
            return INT32_MIN

    raw = int(round(val * Q28_SCALE))
    if clamp:
        return clamp_int32(raw)
    if raw < INT32_MIN or raw > INT32_MAX:
        raise OverflowError(
            f"Value {val} overflows Q28 range [{Q28_MIN_FLOAT}, {Q28_MAX_FLOAT}]"
        )
    return raw


def q28_to_float(q: int) -> float:
    """Converts a 32-bit signed Q28 integer to a Python float.

    Args:
        q: Signed 32-bit integer.

    Returns:
        Float value equal to q / 2^28.
    """
    return float(q) / Q28_SCALE


def float_to_q31(val: float, prescale: int = 1, clamp: bool = True) -> int:
    """Converts a float to a 32-bit signed Q31 fixed-point integer (1.31).

    Args:
        val: Floating point value to convert.
        prescale: Prescaling divisor (e.g. 2 or 4) to accommodate values |val| >= 1.0.
                  The scaled value val / prescale must reside in [-1.0, 1.0).
        clamp: If True, saturates out-of-range values. If False, raises OverflowError.

    Returns:
        Signed 32-bit integer representing the Q31 value.

    Raises:
        ValueError: If prescale <= 0 or val is non-finite.
        OverflowError: If clamp is False and scaled value overflows Q31 range.
    """
    if math.isnan(val) or math.isinf(val):
        raise ValueError(f"Cannot convert non-finite float {val} to Q31")
    if prescale <= 0:
        raise ValueError(f"Prescale factor must be positive, got {prescale}")

    scaled_val = val / float(prescale)
    if clamp:
        if scaled_val >= Q31_MAX_FLOAT:
            return INT32_MAX
        if scaled_val <= Q31_MIN_FLOAT:
            return INT32_MIN

    raw = int(round(scaled_val * Q31_SCALE))
    if clamp:
        return clamp_int32(raw)
    if raw < INT32_MIN or raw > INT32_MAX:
        raise OverflowError(
            f"Scaled value {scaled_val} overflows Q31 range [{Q31_MIN_FLOAT}, {Q31_MAX_FLOAT}]"
        )
    return raw


def q31_to_float(q: int, prescale: int = 1) -> float:
    """Converts a 32-bit signed Q31 integer to a Python float.

    Args:
        q: Signed 32-bit integer.
        prescale: Prescaling factor previously applied during conversion.

    Returns:
        Float value equal to (q / 2^31) * prescale.
    """
    if prescale <= 0:
        raise ValueError(f"Prescale factor must be positive, got {prescale}")
    return (float(q) / Q31_SCALE) * float(prescale)


def pack_biquad_q28(
    a1: float, a2: float, b0: float, b1: float, b2: float
) -> bytes:
    """Packs 5 float coefficients into 20 bytes using Q28 little-endian integers (<5i)."""
    q_tuple = (
        float_to_q28(a1),
        float_to_q28(a2),
        float_to_q28(b0),
        float_to_q28(b1),
        float_to_q28(b2),
    )
    return struct.pack(BIQUAD_STRUCT_FORMAT, *q_tuple)


def unpack_biquad_q28(data: bytes) -> Tuple[float, float, float, float, float]:
    """Unpacks 20 bytes (<5i) into a tuple of 5 float coefficients (a1, a2, b0, b1, b2)."""
    if len(data) != BIQUAD_BYTE_SIZE:
        raise ValueError(
            f"Biquad packed data must be exactly {BIQUAD_BYTE_SIZE} bytes, got {len(data)}"
        )
    q_tuple = struct.unpack(BIQUAD_STRUCT_FORMAT, data)
    return (
        q28_to_float(q_tuple[0]),
        q28_to_float(q_tuple[1]),
        q28_to_float(q_tuple[2]),
        q28_to_float(q_tuple[3]),
        q28_to_float(q_tuple[4]),
    )


def pack_biquad_q31(
    a1: float, a2: float, b0: float, b1: float, b2: float, prescale: int = 2
) -> bytes:
    """Packs 5 float coefficients into 20 bytes using Q31 with prescale."""
    q_tuple = (
        float_to_q31(a1, prescale=prescale),
        float_to_q31(a2, prescale=prescale),
        float_to_q31(b0, prescale=prescale),
        float_to_q31(b1, prescale=prescale),
        float_to_q31(b2, prescale=prescale),
    )
    return struct.pack(BIQUAD_STRUCT_FORMAT, *q_tuple)


def unpack_biquad_q31(
    data: bytes, prescale: int = 2
) -> Tuple[float, float, float, float, float]:
    """Unpacks 20 bytes (<5i) into 5 float coefficients using Q31 with prescale."""
    if len(data) != BIQUAD_BYTE_SIZE:
        raise ValueError(
            f"Biquad packed data must be exactly {BIQUAD_BYTE_SIZE} bytes, got {len(data)}"
        )
    q_tuple = struct.unpack(BIQUAD_STRUCT_FORMAT, data)
    return (
        q31_to_float(q_tuple[0], prescale=prescale),
        q31_to_float(q_tuple[1], prescale=prescale),
        q31_to_float(q_tuple[2], prescale=prescale),
        q31_to_float(q_tuple[3], prescale=prescale),
        q31_to_float(q_tuple[4], prescale=prescale),
    )
