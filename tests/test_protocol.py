import pytest

from mdp_commander.protocol import (
    ChecksumError,
    ProtocolError,
    Type7Status,
    genDispatchChAddr,
    genGenericPacket,
    genGetVoltCurr,
    genSetCurr,
    genSetVolt,
    parseType4Resp,
    parseType6Resp,
    parseType7Resp,
    parseType8Resp,
    parseType9Resp,
)


def test_real_type4_capture():
    assert genGetVoltCurr().hex() == "0401008d"
    assert parseType4Resp(bytes.fromhex("04050200023100b8")) == (0.2, 23.1)


def test_real_idempotent_write_capture():
    assert genSetVolt("62e6491b", 23.1).hex() == "070b62e6491b0001030302310060"
    assert genSetCurr("62e6491b", 0.2).hex() == "070b62e6491b0001020300020050"


def test_type6_returns_the_declared_payload():
    response = genGenericPacket(6, b"ABC")
    assert parseType6Resp(response) == b"ABC"


@pytest.mark.parametrize("voltage", [-0.001, 30.001, float("nan")])
def test_voltage_bounds_are_always_enforced(voltage):
    with pytest.raises(ValueError):
        genSetVolt("00000001", voltage)


@pytest.mark.parametrize("current", [-0.001, 10.001, float("inf")])
def test_current_bounds_are_always_enforced(current):
    with pytest.raises(ValueError):
        genSetCurr("00000001", current)


@pytest.mark.parametrize("current", [0.0, 10.0])
def test_current_bounds_are_inclusive(current):
    assert genSetCurr("00000001", current)[0] == 7


def test_dispatch_validates_channel_and_address():
    assert genDispatchChAddr("153614fae1", 125)[-2] == 125
    with pytest.raises(ValueError):
        genDispatchChAddr("153614fae1", 126)
    with pytest.raises(ValueError):
        genDispatchChAddr("not-an-address", 50)


def test_parser_rejects_truncated_and_corrupt_packets():
    with pytest.raises(ProtocolError):
        parseType4Resp(bytes.fromhex("04050200"))

    corrupt = bytearray.fromhex("04050200023100b8")
    corrupt[-1] ^= 1
    with pytest.raises(ChecksumError):
        parseType4Resp(corrupt)


def test_type7_status_response():
    response = bytes.fromhex(
        "071c00000340020252712022ba3004ba3005ba3005ba3004023100000200c0"
    )
    result = parseType7Resp(response)
    assert result == Type7Status(
        errflag=0,
        locked=False,
        temperature=34.0,
        output_state="cv",
        input_voltage=25.271,
        input_current=2.022,
        voltage=23.1,
        current=0.2,
        realtime_adc=[(0xBA3, 4), (0xBA3, 5), (0xBA3, 5), (0xBA3, 4)],
    )


def test_type7_set_ack_response():
    response = bytes.fromhex(
        "071600000340020252712043ba3004ba3005ba3004ba30059a"
    )
    result = parseType7Resp(response)
    assert result == Type7Status(
        errflag=0,
        locked=False,
        temperature=34.0,
        output_state="cv",
        input_voltage=25.271,
        input_current=2.043,
        voltage=None,
        current=None,
        realtime_adc=[(0xBA3, 4), (0xBA3, 5), (0xBA3, 4), (0xBA3, 5)],
    )


def test_type7_rejects_unknown_output_state():
    payload = bytearray.fromhex(
        "00000340040252712022ba3004ba3005ba3005ba3004023100000200"
    )
    with pytest.raises(ProtocolError):
        parseType7Resp(genGenericPacket(7, bytes(payload)))


def test_type8_realtime_response():
    response = bytes.fromhex(
        "081c00ba3004ba3005ba3005ba3004ba3004ba3004ba3004ba3004ba300412"
    )
    assert parseType8Resp(response, 74, 48545, 14, 30765) == (
        0,
        [(23103, value) for value in (1, 4, 4, 1, 1, 1, 1, 1, 1)],
    )


def test_type9_calibration_response():
    response = bytes.fromhex("090e62e6491b00004abda1000e782d0256")
    assert parseType9Resp(response) == (
        "62e6491b",
        0,
        74,
        48545,
        14,
        30765,
    )
