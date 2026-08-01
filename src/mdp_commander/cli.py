"""Command-line interface for mdp-commander."""

import argparse
from collections import deque
import logging
import os
from threading import Event, Thread

import serial

from .device import P906, RecvError
from .protocol import MAX_CHANNEL, MdpError


SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 0.5


def _open_serial(device: str) -> serial.Serial:
    options = {"exclusive": True} if os.name == "posix" else {}
    return serial.Serial(
        device,
        SERIAL_BAUDRATE,
        serial.EIGHTBITS,
        serial.PARITY_NONE,
        serial.STOPBITS_ONE,
        timeout=SERIAL_TIMEOUT,
        write_timeout=1.0,
        **options,
    )


def doAdapterInfo(serial_device: str, addr: int, channel: int) -> str:
    with _open_serial(serial_device) as serial_port:
        device = P906(serial_port, addr, channel)
        info = device.getAdapterCfg().decode("ascii", errors="replace")
    print(info, end="")
    return info


def doAutoMatch(serial_device: str, addr: int, channel: int) -> str:
    with _open_serial(serial_device) as serial_port:
        device = P906(serial_port, addr, channel)
        idcode = device.autoMatch()
    print(f"matching P906 {idcode} on channel {channel}, set addr to {addr:010x}")
    return idcode


def doGet(serial_device: str, addr: int, channel: int, idcode: int):
    with _open_serial(serial_device) as serial_port:
        device = P906(serial_port, addr, channel, idcode)
        device.connect()
        adc_data = device.getRealtimeValue()
        status = dict(device.status)
    print(status)
    print(f"recently adc data(corrected, in mV/mA): {adc_data}")
    return status, adc_data


def _sample_realtime(
    device: P906,
    batches: deque[list[tuple[int, int]]],
    stop: Event,
) -> None:
    while not stop.is_set():
        try:
            batches.append(device.getRealtimeValue())
        except RecvError:
            continue
        except (MdpError, serial.SerialException) as error:
            logging.getLogger("mdp").error("live sampling stopped: %s", error)
            return


def doLivePlot(serial_device: str, addr: int, channel: int, idcode: int) -> None:
    import matplotlib.animation as animation
    import matplotlib.pyplot as plt

    sample_limit = 1000
    x = deque(maxlen=sample_limit)
    volts = deque(maxlen=sample_limit)
    currs = deque(maxlen=sample_limit)
    batches: deque[list[tuple[int, int]]] = deque(maxlen=32)

    figure, (volt_ax, curr_ax) = plt.subplots(2, 1, sharex=True)
    volt_ax.set_xlim(0, sample_limit)
    volt_ax.set_ylim(0, 30)
    curr_ax.set_ylim(0, 10)
    (volt_line,) = volt_ax.plot([], [])
    (curr_line,) = curr_ax.plot([], [])
    sample_number = 0

    with _open_serial(serial_device) as serial_port:
        device = P906(serial_port, addr, channel, idcode)
        device.connect()
        stop = Event()
        worker = Thread(
            target=_sample_realtime,
            args=(device, batches, stop),
            daemon=True,
        )
        worker.start()

        def animate(_frame):
            nonlocal sample_number
            while batches:
                for voltage, current in batches.popleft():
                    sample_number += 1
                    x.append(sample_number)
                    volts.append(voltage / 1000.0)
                    currs.append(current / 1000.0)
            if x:
                left = max(0, x[-1] - sample_limit)
                volt_ax.set_xlim(left, max(sample_limit, x[-1]))
            volt_line.set_data(x, volts)
            curr_line.set_data(x, currs)
            return volt_line, curr_line

        try:
            live_animation = animation.FuncAnimation(
                figure,
                animate,
                frames=None,
                interval=200,
                cache_frame_data=False,
            )
            plt.show()
            del live_animation
        finally:
            stop.set()
            worker.join()


def doSet(
    serial_device: str,
    addr: int,
    channel: int,
    idcode: int,
    operation: str,
    value: str,
) -> bool:
    if operation in ("volt", "voltage", "curr", "current"):
        parsed_value = float(value)
    elif operation == "switch" and value in ("on", "off"):
        parsed_value = value == "on"
    else:
        raise ValueError(f"invalid {operation} value: {value}")

    with _open_serial(serial_device) as serial_port:
        device = P906(serial_port, addr, channel, idcode)
        device.connect()
        if operation in ("volt", "voltage"):
            return device.setOutputVolt(parsed_value)
        if operation in ("curr", "current"):
            return device.setOutputCurr(parsed_value)
        return device.switch(parsed_value)


def _hex_int(value: str) -> int:
    try:
        return int(value, 16)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be hexadecimal") from error


def _channel(value: str) -> int:
    try:
        channel = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if not 0 <= channel <= MAX_CHANNEL:
        raise argparse.ArgumentTypeError(f"must be between 0 and {MAX_CHANNEL}")
    return channel


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-d",
        "--serial",
        required=True,
        help='serial device for nrf24_adapter, e.g. "/dev/ttyUSB0"',
    )
    common.add_argument(
        "-a",
        "--addr",
        type=_hex_int,
        default=0x153614FAE1,
        help="5-byte nRF24 address in hexadecimal (default: 153614fae1)",
    )
    common.add_argument(
        "-c",
        "--channel",
        type=_channel,
        default=50,
        help=f"nRF24 channel from 0 to {MAX_CHANNEL} (default: 50)",
    )
    common.add_argument(
        "-l",
        "--loglevel",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="log level (default: INFO)",
    )

    parser = argparse.ArgumentParser(prog="mdp")
    actions = parser.add_subparsers(title="action", dest="action", required=True)
    actions.add_parser("adapter-info", parents=[common], help="show adapter configuration")
    actions.add_parser("match", parents=[common], help="pair a P906")

    get_parser = actions.add_parser("get", parents=[common], help="read P906 status")
    get_parser.add_argument("-I", "--idcode", required=True, type=_hex_int)

    plot_parser = actions.add_parser("plot", parents=[common], help="plot live readings")
    plot_parser.add_argument("-I", "--idcode", required=True, type=_hex_int)

    set_parser = actions.add_parser("set", parents=[common], help="change P906 state")
    set_parser.add_argument("-I", "--idcode", required=True, type=_hex_int)
    set_parser.add_argument(
        "operation", choices=("volt", "voltage", "curr", "current", "switch")
    )
    set_parser.add_argument("value", help="numeric value, or on/off for switch")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.loglevel)
    logger = logging.getLogger("mdp")

    try:
        if args.action == "adapter-info":
            doAdapterInfo(args.serial, args.addr, args.channel)
        elif args.action == "match":
            doAutoMatch(args.serial, args.addr, args.channel)
        elif args.action == "get":
            doGet(args.serial, args.addr, args.channel, args.idcode)
        elif args.action == "plot":
            doLivePlot(args.serial, args.addr, args.channel, args.idcode)
        elif args.action == "set":
            doSet(
                args.serial,
                args.addr,
                args.channel,
                args.idcode,
                args.operation,
                args.value,
            )
    except (MdpError, serial.SerialException, ValueError) as error:
        if logger.isEnabledFor(logging.DEBUG):
            logger.exception("command failed")
        else:
            logger.error("%s", error)
        return 1
    return 0
