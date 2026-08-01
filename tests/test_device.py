from collections import deque

import pytest

from mdp_commander.device import AdapterError, P906, PairingError, RecvError
from mdp_commander.protocol import ProtocolError, genGenericPacket


class ScriptedSerial:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.writes = []
        self.reset_count = 0

    def read_until(self, marker):
        return self.responses.popleft() if self.responses else b""

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        self.reset_count += 1


def radio_line(packet_type, payload=b"", pipe=1):
    frame = genGenericPacket(packet_type, payload)
    padded = frame.hex().ljust(64, "0")
    return f"{pipe},{padded}\r\n".encode()


def type7_response(errflag=0, output_state=0):
    payload = bytes([errflag]) + bytes.fromhex(
        f"000000{output_state:02x}"
        "025000"
        "0030"
        "123456123456123456123456"
        "023100"
        "000200"
    )
    return genGenericPacket(7, payload)


def test_adapter_command_requires_ok():
    serial = ScriptedSerial(b"Ready\r\n", b"")
    device = P906(serial, 0x153614FAE1, 50, 1)

    with pytest.raises(AdapterError):
        device.configAdapter()
    assert serial.writes == [b"AT+TEST\r\n"]


def test_adapter_command_retries_after_late_ready():
    serial = ScriptedSerial(b"", b"Ready\r\n", b"OK\r\n")
    device = P906(serial, 0x153614FAE1, 50, 1)

    assert device._adapter_command("AT+TEST") == b"OK\r\n"
    assert serial.writes == [b"AT+TEST\r\n", b"AT+TEST\r\n"]


def test_radio_response_is_still_read_when_hardware_ack_is_lost():
    serial = ScriptedSerial(
        b"Ready\r\n",
        b"ERROR,TX_NO_ACK\r\n",
        radio_line(5, bytes.fromhex("12345678")),
    )
    device = P906(serial, 0x153614FAE1, 50, 1)

    _, response = device.sr(
        genGenericPacket(5, b"M"), expected_types=(5,), retries=1
    )

    assert response[0] == 5


def test_old_firmware_tx_echo_before_ok_remains_supported():
    serial = ScriptedSerial(
        b"Ready\r\n",
        b"05014d8900000000000000000000000000000000000000000000000000000000OK\r\n",
        radio_line(5, bytes.fromhex("12345678")),
    )
    device = P906(serial, 0x153614FAE1, 50, 1)

    _, response = device.sr(
        genGenericPacket(5, b"M"), expected_types=(5,), retries=1
    )

    assert response[0] == 5


def test_malformed_radio_data_becomes_receive_error():
    serial = ScriptedSerial(b"Ready\r\n", b"1,\r\n")
    device = P906(serial, 0x153614FAE1, 50, 1, retries=1)

    with pytest.raises(RecvError):
        device.recv()


def test_receive_skips_valid_but_unexpected_packets():
    serial = ScriptedSerial(
        b"Ready\r\n",
        radio_line(8, b"\x00"),
        radio_line(5, bytes.fromhex("12345678")),
    )
    device = P906(serial, 0x153614FAE1, 50, 1)

    _, response = device.recv(retries=2, expected_types=(5,))
    assert response[0] == 5


def test_unknown_switch_response_is_not_success(monkeypatch):
    responses = [b"Ready\r\n", b"OK\r\n", radio_line(99)]
    responses.extend((b"OK\r\n", b"") * 5)
    serial = ScriptedSerial(*responses)
    device = P906(serial, 0x153614FAE1, 50, 1, retries=1)
    monkeypatch.setattr("mdp_commander.device.sleep", lambda _: None)

    with pytest.raises(RecvError):
        device.switch(True)


def test_device_error_flag_is_not_success():
    device = P906(ScriptedSerial(b"Ready\r\n"), 0x153614FAE1, 50, 1)
    device.sr = lambda *args, **kwargs: (1, type7_response(errflag=1))

    with pytest.raises(ProtocolError):
        device.switch(True)


@pytest.mark.parametrize(
    ("state", "name", "enabled"),
    ((0, "off", False), (1, "cc", True), (2, "cv", True), (3, "on", True)),
)
def test_output_state_is_not_reduced_to_one_bit(state, name, enabled):
    device = P906(ScriptedSerial(b"Ready\r\n"), 0x153614FAE1, 50, 1)

    device._update_type7(type7_response(output_state=state))

    assert device.status["OutputState"] == name
    assert device.status["OutputEnabled"] is enabled


def test_type7_updates_extended_status_from_real_capture():
    device = P906(ScriptedSerial(b"Ready\r\n"), 0x153614FAE1, 50, 1)
    device.status.update(HVzero16=74, HVgain16=48545, HCzero04=14, HCgain04=30765)
    response = bytes.fromhex(
        "071c00000340020252712022ba3004ba3005ba3005ba3004023100000200c0"
    )

    device._update_type7(response)

    assert device.status["Locked"] is False
    assert device.status["Temperature"] == 34.0
    assert device.status["RealtimeValues"] == [
        (23103, 1),
        (23103, 4),
        (23103, 4),
        (23103, 1),
    ]


def test_switch_verifies_state_when_ack_is_lost():
    device = P906(ScriptedSerial(b"Ready\r\n"), 0x153614FAE1, 50, 1)
    responses = iter(
        (
            RecvError("lost acknowledgement"),
            (1, type7_response(output_state=2)),
        )
    )

    def scripted_request(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    device.sr = scripted_request
    assert device.switch(True) is True
    assert device.status["OutputEnabled"] is True


def test_switch_waits_for_delayed_state_change(monkeypatch):
    device = P906(ScriptedSerial(b"Ready\r\n"), 0x153614FAE1, 50, 1)
    responses = iter(
        (
            type7_response(),
            type7_response(),
            type7_response(),
            type7_response(output_state=2),
        )
    )
    device.sr = lambda *args, **kwargs: (1, next(responses))
    waits = []
    monkeypatch.setattr("mdp_commander.device.sleep", waits.append)

    assert device.switch(True) is True
    assert waits == [0.15, 0.15]


def test_voltage_verifies_set_point_when_ack_is_lost():
    device = P906(ScriptedSerial(b"Ready\r\n"), 0x153614FAE1, 50, 1)
    responses = iter(
        (
            RecvError("lost acknowledgement"),
            (1, type7_response()),
        )
    )

    def scripted_request(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    device.sr = scripted_request
    assert device.setOutputVolt(23.1) is True
    assert device.status["Voltage"] == 23.1


def test_failed_pairing_never_dispatches_type6():
    serial = ScriptedSerial(b"Ready\r\n")
    device = P906(serial, 0x153614FAE1, 50)
    packet_types = []
    device._adapter_command = lambda command: b"OK\r\n"

    def fail_receive(packet, expected_types=None, retries=None):
        packet_types.append(packet[0])
        raise RecvError("timeout")

    device.sr = fail_receive
    with pytest.raises(PairingError):
        device.autoMatch(retries=2)
    assert packet_types == [5, 5]


def test_channel_zero_is_valid_during_connect():
    serial = ScriptedSerial(b"Ready\r\n")
    device = P906(serial, 0x153614FAE1, 0, 1)
    device.configAdapter = lambda: None
    device.getGainOffset = lambda: (0, 0, 0, 0)
    device.getSetValue = lambda: (0, 0.0, 0.0, 0.0, 0.0)

    device.connect()


def test_connect_requires_device_id():
    device = P906(ScriptedSerial(b"Ready\r\n"), 0x153614FAE1, 50)
    with pytest.raises(ValueError):
        device.connect()
