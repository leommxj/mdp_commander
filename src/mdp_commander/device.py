"""Serial adapter and high-level P906 operations."""

import logging
from time import sleep

import serial as pyserial

from .protocol import (
    MAX_CHANNEL,
    MdpError,
    ProtocolError,
    curr_adc_correct,
    genCallForId,
    genDispatchChAddr,
    genGet7,
    genGet8,
    genSetCurr,
    genSetLedColor,
    genSetVolt,
    genSwitch,
    parseType5Resp,
    parseType6Resp,
    parseType7Resp,
    parseType8Resp,
    parseType9Resp,
    validatePacket,
    volt_adc_correct,
)


READY = b"Ready\r\n"
OK = b"OK\r\n"
LINE_END = b"\r\n"
TX_NO_ACK = b"ERROR,TX_NO_ACK"
CONTROL_RETRIES = 5
CONTROL_RETRY_DELAY = 0.15


class AdapterError(MdpError):
    """Raised when the serial adapter rejects or ignores a command."""


class TxNoAckError(AdapterError):
    """Raised when the nRF24 transmitter receives no hardware acknowledgement."""


class RecvError(MdpError):
    """Raised when no valid expected radio response is received."""


class PairingError(MdpError):
    """Raised when pairing cannot safely complete."""


def _format_hex(value: int | str, digits: int, name: str) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= value < 1 << (digits * 4):
            raise ValueError(f"{name} must fit in {digits} hexadecimal digits")
        return f"{value:0{digits}x}"
    if isinstance(value, str) and len(value) == digits:
        try:
            parsed = bytes.fromhex(value)
        except ValueError as error:
            raise ValueError(f"{name} must be hexadecimal") from error
        if len(parsed) != digits // 2:
            raise ValueError(f"{name} must be hexadecimal without separators")
        return value.lower()
    raise ValueError(f"{name} must contain exactly {digits} hexadecimal digits")


class P906:
    def __init__(
        self,
        serial: pyserial.Serial,
        addr: int | str,
        channel: int,
        idcode: int | str | None = None,
        ch_on_m01: int = 0,
        led_color: int = 0x3168,
        retries: int = 3,
        log_level: int = logging.NOTSET,
    ):
        if (
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or not 0 <= channel <= MAX_CHANNEL
        ):
            raise ValueError(f"channel must be between 0 and {MAX_CHANNEL}")
        if (
            not isinstance(ch_on_m01, int)
            or isinstance(ch_on_m01, bool)
            or not 0 <= ch_on_m01 <= 0xFF
        ):
            raise ValueError("M01 channel must be between 0 and 255")
        if (
            not isinstance(led_color, int)
            or isinstance(led_color, bool)
            or not 0 <= led_color <= 0xFFFF
        ):
            raise ValueError("LED color must be between 0 and 65535")
        if not isinstance(retries, int) or isinstance(retries, bool) or retries < 1:
            raise ValueError("retries must be at least 1")

        self.serial = serial
        self.idcode = None if idcode is None else _format_hex(idcode, 8, "device ID")
        self.addr = _format_hex(addr, 10, "address")
        self.ch = channel
        self.ch_on_m01 = ch_on_m01
        self.led_color = led_color
        self.retries = retries
        self.logger = logging.getLogger("P906")
        self.logger.setLevel(log_level)
        self.status = {
            "HVzero16": None,
            "HVgain16": None,
            "HCzero04": None,
            "HCgain04": None,
            "Voltage": None,
            "Current": None,
            "InputVoltage": None,
            "InputCurrent": None,
            "OutputEnabled": None,
            "OutputState": None,
            "Locked": None,
            "Temperature": None,
            "RealtimeValues": None,
            "ErrFlag": None,
        }

        startup = self.serial.read_until(READY)
        if startup and not startup.endswith(READY):
            self.logger.debug("unexpected adapter startup data: %r", startup)

    def serwrite(self, data: str | bytes) -> None:
        if isinstance(data, str):
            wire_data = data.encode("ascii")
        elif isinstance(data, bytes):
            wire_data = data
        else:
            raise TypeError("serial data must be str or bytes")
        written = self.serial.write(wire_data)
        if written is not None and written != len(wire_data):
            raise AdapterError(f"short serial write: {written}/{len(wire_data)} bytes")

    def _adapter_command(self, command: str) -> bytes:
        response = b""
        for attempt in range(2):
            self.serwrite(command + "\r\n")
            self.serial.flush()
            chunks = []
            while True:
                line = self.serial.read_until(LINE_END)
                if not line:
                    break
                chunks.append(line)
                terminal = line.rstrip(b"\r\n")
                if (
                    terminal.endswith(b"OK")
                    or terminal == b"Ready"
                    or terminal.startswith(b"ERROR")
                ):
                    break
            response = b"".join(chunks)
            if response.endswith(OK):
                return response
            if attempt == 0 and response.endswith(READY):
                continue
            break
        if TX_NO_ACK in response.splitlines():
            raise TxNoAckError("nRF24 transmission received no acknowledgement")
        raise AdapterError(f"adapter did not acknowledge {command!r}: {response!r}")

    def configAdapter(self) -> None:
        self._adapter_command("AT+TEST")
        self._adapter_command(f"AT+CFG=5,{self.ch},3,1,2,1,32")
        self._adapter_command(f"AT+RXADDR=1,{self.addr}")
        self._adapter_command(f"AT+TXADDR={self.addr}")
        self._adapter_command("AT+LISTEN=start")

    def getAdapterCfg(self) -> bytes:
        self._adapter_command("AT+TEST")
        return self._adapter_command("AT+CFG")

    def send(self, data: bytes) -> None:
        self._adapter_command(f"AT+TX={data.hex()}")

    @staticmethod
    def _parse_radio_line(raw: bytes) -> tuple[int, bytes]:
        try:
            pipe_raw, payload_raw = raw.strip().split(b",", 1)
            pipe = int(pipe_raw)
            padded = bytes.fromhex(payload_raw.decode("ascii"))
            payload_length = padded[1]
            frame = padded[: payload_length + 3]
        except (UnicodeDecodeError, ValueError, IndexError) as error:
            raise ProtocolError(f"invalid adapter radio line: {raw!r}") from error
        if not 0 <= pipe <= 5:
            raise ProtocolError(f"invalid radio pipe: {pipe}")
        if len(padded) < payload_length + 3:
            raise ProtocolError("truncated radio frame")
        return pipe, validatePacket(frame)

    def recv(
        self,
        retries: int | None = None,
        expected_types: tuple[int, ...] | None = None,
    ) -> tuple[int, bytes]:
        attempts = self.retries if retries is None else retries
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
            raise ValueError("retries must be at least 1")

        last_error: Exception | None = None
        last_data = b""
        for _ in range(attempts):
            last_data = self.serial.read_until(b"\r\n")
            if not last_data:
                last_error = TimeoutError("radio receive timed out")
                continue
            try:
                pipe, frame = self._parse_radio_line(last_data)
            except ProtocolError as error:
                last_error = error
                continue
            if expected_types is not None and frame[0] not in expected_types:
                last_error = ProtocolError(
                    f"unexpected response type {frame[0]}, expected {expected_types}"
                )
                continue
            self.logger.debug("recv from pipe %s: %s", pipe, frame.hex(" "))
            return pipe, frame

        raise RecvError(f"no valid expected response; last data: {last_data!r}") from last_error

    def sr(
        self,
        data: bytes,
        expected_types: tuple[int, ...] | None = None,
        retries: int | None = None,
    ) -> tuple[int, bytes]:
        reset_input = getattr(self.serial, "reset_input_buffer", None)
        if reset_input is not None:
            reset_input()
        try:
            self.send(data)
        except TxNoAckError as error:
            self.logger.debug("%s; waiting for a possible device response", error)
        self.logger.debug("send: %s", data.hex(" "))
        return self.recv(retries=retries, expected_types=expected_types)

    def getGainOffset(self) -> tuple[int, int, int, int]:
        _, response = self.sr(
            genSetLedColor(self.idcode, self.led_color, self.ch_on_m01),
            expected_types=(9,),
        )
        idcode, _, hv_zero, hv_gain, hc_zero, hc_gain = parseType9Resp(response)
        if self.idcode != idcode:
            raise ProtocolError(f"response ID {idcode} does not match {self.idcode}")
        self.status.update(
            HVzero16=hv_zero,
            HVgain16=hv_gain,
            HCzero04=hc_zero,
            HCgain04=hc_gain,
        )
        return hv_zero, hv_gain, hc_zero, hc_gain

    def _update_type7(
        self, response: bytes
    ) -> tuple[int, float, float, float | None, float | None]:
        parsed = parseType7Resp(response)
        calibration = (
            self.status["HVzero16"],
            self.status["HVgain16"],
            self.status["HCzero04"],
            self.status["HCgain04"],
        )
        realtime_values = None
        if all(value is not None for value in calibration):
            realtime_values = [
                (
                    volt_adc_correct(voltage, calibration[1], calibration[0]),
                    curr_adc_correct(current, calibration[3], calibration[2]),
                )
                for voltage, current in parsed.realtime_adc
            ]
        self.status.update(
            ErrFlag=parsed.errflag,
            InputVoltage=parsed.input_voltage,
            InputCurrent=parsed.input_current,
            Voltage=parsed.voltage,
            Current=parsed.current,
            OutputEnabled=parsed.output_state != "off",
            OutputState=parsed.output_state,
            Locked=parsed.locked,
            Temperature=parsed.temperature,
            RealtimeValues=realtime_values,
        )
        return (
            parsed.errflag,
            parsed.input_voltage,
            parsed.input_current,
            parsed.voltage,
            parsed.current,
        )

    def getSetValue(self) -> tuple[int, float, float, float | None, float | None]:
        _, response = self.sr(genGet7(self.idcode, self.ch_on_m01), expected_types=(7,))
        return self._update_type7(response)

    def getRealtimeValue(self) -> list[tuple[int, int]]:
        calibration = (
            self.status["HVzero16"],
            self.status["HVgain16"],
            self.status["HCzero04"],
            self.status["HCgain04"],
        )
        if any(value is None for value in calibration):
            raise ProtocolError("calibration values are not available; call connect() first")
        _, response = self.sr(genGet8(self.idcode, self.ch_on_m01), expected_types=(8,))
        errflag, values = parseType8Resp(response, *calibration)
        self.status["ErrFlag"] = errflag
        return values

    def _send_control(self, packet: bytes) -> None:
        try:
            _, response = self.sr(packet, expected_types=(7,))
        except RecvError:
            return
        if self._update_type7(response)[0]:
            raise ProtocolError(f"P906 reported error flag {self.status['ErrFlag']}")

    def _verify_control(self, matches, description: str) -> bool:
        last_error: Exception | None = None
        for attempt in range(CONTROL_RETRIES):
            try:
                self.getSetValue()
            except RecvError as error:
                last_error = error
            else:
                if self.status["ErrFlag"]:
                    raise ProtocolError(
                        f"P906 reported error flag {self.status['ErrFlag']}"
                    )
                if matches():
                    return True
                last_error = ProtocolError(f"P906 did not apply {description}")
            if attempt < CONTROL_RETRIES - 1:
                sleep(CONTROL_RETRY_DELAY)
        raise last_error or ProtocolError(f"could not verify {description}")

    def switch(self, on: bool) -> bool:
        self._send_control(genSwitch(self.idcode, on, self.ch_on_m01))
        return self._verify_control(
            lambda: self.status["OutputEnabled"] is on,
            f"output state {on}",
        )

    def setOutputVolt(self, voltage: float) -> bool:
        expected = float(f"{float(voltage):.3f}")
        self._send_control(genSetVolt(self.idcode, voltage, self.ch_on_m01))
        return self._verify_control(
            lambda: self.status["Voltage"] == expected,
            f"voltage {expected}",
        )

    def setOutputCurr(self, current: float) -> bool:
        expected = float(f"{float(current):.3f}")
        self._send_control(genSetCurr(self.idcode, current, self.ch_on_m01))
        return self._verify_control(
            lambda: self.status["Current"] == expected,
            f"current {expected}",
        )

    def connect(self) -> None:
        if self.idcode is None:
            raise ValueError("device ID is required; pair the device first")
        self.configAdapter()
        self.getGainOffset()
        self.getSetValue()

    def autoMatch(self, retries: int = 320) -> str:
        if not isinstance(retries, int) or isinstance(retries, bool) or retries < 1:
            raise ValueError("pairing retries must be at least 1")
        self._adapter_command("AT+TEST")
        self._adapter_command("AT+CFG=5,78,3,1,2,1,32")
        self._adapter_command("AT+RXADDR=1,FFFFFFFFFF")
        self._adapter_command("AT+TXADDR=FFFFFFFFFF")
        self._adapter_command("AT+LISTEN=start")

        self.idcode = None
        for _ in range(retries):
            try:
                _, response = self.sr(genCallForId(), expected_types=(5,), retries=1)
            except RecvError:
                continue
            self.idcode = parseType5Resp(response)
            break
        if self.idcode is None:
            self.configAdapter()
            raise PairingError("no P906 responded; address and channel were not changed")

        try:
            _, response = self.sr(
                genDispatchChAddr(self.addr, self.ch), expected_types=(6,)
            )
        finally:
            self.configAdapter()
        parseType6Resp(response)
        return self.idcode
