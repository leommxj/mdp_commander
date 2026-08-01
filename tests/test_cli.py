import argparse
from collections import deque
from threading import Event

import pytest

from mdp_commander import cli
from mdp_commander.device import AdapterError


def test_top_level_help(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["--help"])
    assert exit_info.value.code == 0
    assert "adapter-info" in capsys.readouterr().out


def test_invalid_set_operation_is_rejected():
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(
            ["set", "invalid", "1", "-d", "/dev/null", "-I", "1"]
        )
    assert exit_info.value.code == 2


def test_runtime_failure_returns_nonzero(monkeypatch):
    def fail(*args):
        raise AdapterError("adapter did not respond")

    monkeypatch.setattr(cli, "doAdapterInfo", fail)
    assert cli.main(["adapter-info", "-d", "/dev/ttyUSB0"]) == 1


def test_invalid_switch_value_does_not_open_serial(monkeypatch):
    def must_not_open(*args):
        raise AssertionError("serial port should not be opened")

    monkeypatch.setattr(cli, "_open_serial", must_not_open)
    assert (
        cli.main(
            ["set", "switch", "invalid", "-d", "/dev/ttyUSB0", "-I", "1"]
        )
        == 1
    )


def test_channel_accepts_full_nrf_range():
    assert cli._channel("125") == 125
    with pytest.raises(argparse.ArgumentTypeError):
        cli._channel("126")


def test_realtime_sampler_recovers_and_bounds_queued_data():
    stop = Event()
    batches = deque(maxlen=2)

    class Device:
        calls = 0

        def getRealtimeValue(self):
            self.calls += 1
            if self.calls == 1:
                raise cli.RecvError("timeout")
            if self.calls == 4:
                stop.set()
            return [(self.calls, 0)]

    cli._sample_realtime(Device(), batches, stop)

    assert list(batches) == [[(3, 0)], [(4, 0)]]
