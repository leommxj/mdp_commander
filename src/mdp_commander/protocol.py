"""MDP-P906 packet generation and parsing."""

from dataclasses import dataclass
import math


MAX_PAYLOAD_SIZE = 28
MAX_CHANNEL = 125
OUTPUT_STATES = ("off", "cc", "cv", "on")


class MdpError(Exception):
    """Base error for MDP communication."""


class ProtocolError(MdpError):
    """Raised when a packet does not follow the MDP protocol."""


class ChecksumError(ProtocolError):
    """Raised when a packet checksum is invalid."""


@dataclass(frozen=True, slots=True)
class Type7Status:
    errflag: int
    locked: bool
    temperature: float
    output_state: str
    input_voltage: float
    input_current: float
    voltage: float | None
    current: float | None
    realtime_adc: list[tuple[int, int]]


def mdpChecksum(data: bytes) -> bytes:
    checksum = 0x88
    for value in data:
        checksum ^= value
    return bytes([checksum])


def validatePacket(
    data: bytes,
    expected_type: int | None = None,
    expected_lengths: tuple[int, ...] | None = None,
) -> bytes:
    frame = bytes(data)
    if len(frame) < 3:
        raise ProtocolError("packet is too short")

    packet_type, payload_length = frame[:2]
    if len(frame) != payload_length + 3:
        raise ProtocolError(
            f"packet length mismatch: declared {payload_length}, got {len(frame) - 3}"
        )
    if expected_type is not None and packet_type != expected_type:
        raise ProtocolError(
            f"unexpected packet type: expected {expected_type}, got {packet_type}"
        )
    if expected_lengths is not None and payload_length not in expected_lengths:
        raise ProtocolError(
            f"unexpected Type {packet_type} payload length: {payload_length}"
        )
    if mdpChecksum(frame[:-1]) != frame[-1:]:
        raise ChecksumError("packet checksum mismatch")
    return frame


def genGenericPacket(packet_type: int, data: bytes) -> bytes:
    if not 0 <= packet_type <= 0xFF:
        raise ValueError("packet type must fit in one byte")
    if not isinstance(data, bytes):
        raise TypeError("packet payload must be bytes")
    if len(data) > MAX_PAYLOAD_SIZE:
        raise ValueError(f"packet payload cannot exceed {MAX_PAYLOAD_SIZE} bytes")

    packet = bytes((packet_type, len(data))) + data
    return packet + mdpChecksum(packet)


def _hex_bytes(value: str, size: int, name: str) -> bytes:
    if not isinstance(value, str) or len(value) != size * 2:
        raise ValueError(f"{name} must contain exactly {size * 2} hexadecimal digits")
    try:
        result = bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f"{name} must be hexadecimal") from error
    if len(result) != size:
        raise ValueError(f"{name} must be hexadecimal without separators")
    return result


def _byte(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFF:
        raise ValueError(f"{name} must be between 0 and 255")
    return value


def _encode_decimal(value: float, minimum: float, maximum: float, name: str) -> bytes:
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    digits = f"{number:07.3f}".replace(".", "")
    if len(digits) != 6:
        raise ValueError(f"{name} cannot be encoded")
    return bytes.fromhex(digits)


def _decode_decimal(data: bytes, integer_digits: int, name: str) -> float:
    digits = data.hex()
    if any(digit not in "0123456789" for digit in digits):
        raise ProtocolError(f"{name} is not packed decimal")
    return float(f"{digits[:integer_digits]}.{digits[integer_digits:]}")


def _decode_adc(data: bytes) -> tuple[int, int]:
    packed = data.hex()
    return int(packed[:3], 16), int(packed[3:], 16)


def genGetVoltCurr() -> bytes:
    return genGenericPacket(4, b"\x00")


def parseType4Resp(data: bytes) -> tuple[float, float]:
    frame = validatePacket(data, 4, (5,))
    current = _decode_decimal(frame[2:4], 1, "current")
    voltage = _decode_decimal(frame[4:7], 3, "voltage")
    return current, voltage


def genCallForId() -> bytes:
    return genGenericPacket(5, b"M")


def parseType5Resp(data: bytes) -> str:
    frame = validatePacket(data, 5, (4,))
    return frame[2:6].hex()


def genDispatchChAddr(addr: str, ch: int) -> bytes:
    if not isinstance(ch, int) or isinstance(ch, bool) or not 0 <= ch <= MAX_CHANNEL:
        raise ValueError(f"channel must be between 0 and {MAX_CHANNEL}")
    address = _hex_bytes(addr, 5, "address")
    return genGenericPacket(6, address[::-1] + bytes([ch]))


def parseType6Resp(data: bytes) -> bytes:
    frame = validatePacket(data, 6, (3,))
    return frame[2:5]


def _device_prefix(idcode: str, ch_on_m01: int, blink: bool) -> bytes:
    if not isinstance(blink, bool):
        raise TypeError("blink must be a bool")
    return _hex_bytes(idcode, 4, "device ID") + bytes(
        (_byte(ch_on_m01, "M01 channel"), int(blink))
    )


def genSetVolt(idcode: str, voltage: float, ch_on_m01: int = 0, blink: bool = True) -> bytes:
    payload = _device_prefix(idcode, ch_on_m01, blink)
    payload += b"\x03\x03" + _encode_decimal(voltage, 0.0, 30.0, "voltage")
    return genGenericPacket(7, payload)


def genSetCurr(idcode: str, current: float, ch_on_m01: int = 0, blink: bool = True) -> bytes:
    payload = _device_prefix(idcode, ch_on_m01, blink)
    payload += b"\x02\x03" + _encode_decimal(current, 0.0, 10.0, "current")
    return genGenericPacket(7, payload)


def genSwitch(idcode: str, on: bool, ch_on_m01: int = 0, blink: bool = True) -> bytes:
    if not isinstance(on, bool):
        raise TypeError("on must be a bool")
    payload = _device_prefix(idcode, ch_on_m01, blink)
    payload += b"\x0c\x00" + bytes([int(on)])
    return genGenericPacket(7, payload)


def genGet7(idcode: str, ch_on_m01: int = 0, blink: bool = True) -> bytes:
    return genGenericPacket(7, _device_prefix(idcode, ch_on_m01, blink))


def parseType7Resp(data: bytes) -> Type7Status:
    frame = validatePacket(data, 7, (0x16, 0x1C))
    payload = frame[2:-1]
    state = payload[4]
    if state >= len(OUTPUT_STATES):
        raise ProtocolError(f"unknown output state: {state}")
    input_volt = _decode_decimal(payload[5:8], 3, "input voltage")
    input_curr = _decode_decimal(payload[8:10], 1, "input current")
    realtime_adc = [_decode_adc(payload[i : i + 3]) for i in range(10, 22, 3)]

    if len(payload) == 0x1C:
        voltage = _decode_decimal(payload[22:25], 3, "voltage")
        current = _decode_decimal(payload[25:28], 3, "current")
    else:
        voltage = current = None
    return Type7Status(
        errflag=payload[0],
        locked=bool(payload[1]),
        temperature=_decode_decimal(payload[2:4], 3, "temperature"),
        output_state=OUTPUT_STATES[state],
        input_voltage=input_volt,
        input_current=input_curr,
        voltage=voltage,
        current=current,
        realtime_adc=realtime_adc,
    )


def genGet8(idcode: str, ch_on_m01: int = 0, blink: bool = True) -> bytes:
    return genGenericPacket(8, _device_prefix(idcode, ch_on_m01, blink))


def parseType8Resp(
    data: bytes,
    HVzero16: int,
    HVgain16: int,
    HCzero04: int,
    HCgain04: int,
) -> tuple[int, list[tuple[int, int]]]:
    frame = validatePacket(data, 8)
    payload = frame[2:-1]
    if not 1 <= len(payload) <= 0x1C or (len(payload) - 1) % 3:
        raise ProtocolError(f"invalid Type 8 payload length: {len(payload)}")
    if any(value is None for value in (HVzero16, HVgain16, HCzero04, HCgain04)):
        raise ProtocolError("calibration values are not available")

    values = []
    for offset in range(1, len(payload), 3):
        voltage_adc, current_adc = _decode_adc(payload[offset : offset + 3])
        values.append(
            (
                volt_adc_correct(voltage_adc, HVgain16, HVzero16),
                curr_adc_correct(current_adc, HCgain04, HCzero04),
            )
        )
    return payload[0], values


def genSetLedColor(
    idcode: str,
    led_color: int = 0x3168,
    ch_on_m01: int = 0,
    blink: bool = True,
) -> bytes:
    if (
        not isinstance(led_color, int)
        or isinstance(led_color, bool)
        or not 0 <= led_color <= 0xFFFF
    ):
        raise ValueError("LED color must be between 0 and 65535")
    payload = _device_prefix(idcode, ch_on_m01, blink) + led_color.to_bytes(2, "big")
    return genGenericPacket(9, payload)


def parseType9Resp(data: bytes) -> tuple[str, int, int, int, int, int]:
    frame = validatePacket(data, 9, (14,))
    payload = frame[2:-1]
    if payload[13] != 2:
        raise ProtocolError(f"unexpected Type 9 trailer: {payload[13]}")
    return (
        payload[:4].hex(),
        payload[4],
        int.from_bytes(payload[5:7], "big"),
        int.from_bytes(payload[7:9], "big"),
        int.from_bytes(payload[9:11], "big"),
        int.from_bytes(payload[11:13], "big"),
    )


def volt_adc_correct(value: int, gain: int, offset: int) -> int:
    return max(0, round((value * 16 - offset) * gain / 100000.0))


def curr_adc_correct(value: int, gain: int, offset: int) -> int:
    return max(0, round((value * 4 - offset) * gain / 100000.0 * 2))
