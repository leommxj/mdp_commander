#!/usr/bin/env python
# -*- coding: utf-8 -*-
import serial
import argparse
import struct
import logging
from hexdump import hexdump


def mdpChecksum(data:bytes):
    r = 0x88
    for c in data:
        r ^= c
    return bytes([r])

def genGenericPacket(typ, data):
    assert(len(data)<29)
    packet = struct.pack('B', typ) + struct.pack('B', len(data)) + data
    packet += mdpChecksum(packet)
    return packet


# type 4
def genGetVoltCurr():
    packet = genGenericPacket(4, bytes.fromhex('00'))
    return packet

def parseType4Resp(data):
    """
        Type 4 response contains SET VALUE of CURRENT and VOLTAGE (not realtime value)
    """
    assert(data[0] == 4)
    assert(data[1] == 5)
    current = data[2:4].hex()
    current = float(current[0]+'.'+current[1:])
    voltage = data[4:7].hex()
    voltage = float(voltage[0:3]+'.'+voltage[3:])
    return (current, voltage)


# type 5
def genCallForId():
    packet = genGenericPacket(5, bytes.fromhex('4D'))
    return packet

def parseType5Resp(data):
    assert(data[0]==5)
    assert(data[1]==4)
    idcode = data[2:6].hex()
    return idcode


# type 6
def genDispatchChAddr(addr, ch):
    d = '{}{:02x}'.format(bytes.fromhex(addr)[::-1].hex(), ch)
    packet = genGenericPacket(6, bytes.fromhex(d))
    return packet

def parseType6Resp(data):
    assert(data[0]==6)
    assert(data[1]==3)# or FAIL ?
    return data[2:data[1]]


# type 7
def genSetVolt(idcode, voltage, ch_on_m01=0, blink=True):
    assert(0.0<=voltage<=30.0)
    v = "{:07.3f}".format(voltage).replace('.','')
    # subtype 03 len 03
    d = '{}{:02x}{:02x}0303{}'.format(idcode, ch_on_m01, 1 if blink else 0, v)
    packet = genGenericPacket(7, bytes.fromhex(d))
    return packet

def genSetCurr(idcode, current, ch_on_m01=0, blink=True):
    assert(0.0<current<10.0)
    c = "{:07.3f}".format(current).replace('.','')
    # subtype 02 len 03
    d = '{}{:02x}{:02x}0203{}'.format(idcode, ch_on_m01, 1 if blink else 0, c)
    packet = genGenericPacket(7, bytes.fromhex(d))
    return packet

def genSwitch(idcode, on:bool, ch_on_m01=0, blink=True):
    # subtype 0c len 03
    d = '{}{:02x}{:02x}0C00{:02x}'.format(idcode, ch_on_m01, 1 if blink else 0, 1 if on else 0)
    packet = genGenericPacket(7, bytes.fromhex(d))
    return packet

def genGet7(idcode, ch_on_m01=0, blink=True):
    d = '{}{:02x}{:02x}'.format(idcode, ch_on_m01, 1 if blink else 0)
    packet = genGenericPacket(7, bytes.fromhex(d))
    return packet

def parseType7Resp(data):
    assert(data[0] == 7)
    if data[1] == 0x1c:
        errflag = data[2]
        unk1 = data[3]
        unk2 = data[4:6]
        unk3 = data[6]
        input_volt = data[7:10].hex()
        input_volt = float(input_volt[0:3]+'.'+input_volt[3:])
        input_curr = data[10:12].hex()
        input_curr = float(input_curr[0]+'.'+input_curr[1:])
        realtime_adc = []
        for i in range(4):
            sd = data[12+i*3:12+i*3+3].hex()
            sv = int(sd[0:3], 16)
            sc = int(sd[3:6], 16)
            realtime_adc.append((sv,sc))
        voltage = data[24:27].hex()
        voltage = float(voltage[0:3]+'.'+voltage[3:])
        current = data[27:30].hex()
        current = float(current[0:3]+'.'+current[3:])
        return (errflag, input_volt, input_curr, voltage, current, realtime_adc)
    elif data[1] == 0x16:
        errflag = data[2]
        unk1 = data[3]
        unk2 = data[4:6]
        unk3 = data[6]
        input_volt = data[7:10].hex()
        input_volt = float(input_volt[0:3]+'.'+input_volt[3:])
        input_curr = data[10:12].hex()
        input_curr = float(input_curr[0]+'.'+input_curr[1:])
        realtime_adc = []
        for i in range(4):
            sd = data[12+i*3:12+i*3+3].hex()
            sv = int(sd[0:3], 16)
            sc = int(sd[3:6], 16)
            realtime_adc.append((sv,sc))
        return (errflag, input_volt, input_curr, None, None, realtime_adc)
    return None


# type 8
def genGet8(idcode, ch_on_m01=0, blink=True):
    d = '{}{:02x}{:02x}'.format(idcode, ch_on_m01, 1 if blink else 0)
    packet = genGenericPacket(8, bytes.fromhex(d))
    return packet

def parseType8Resp(data, HVzero16, HVgain16, HCzero04, HCgain04):
    assert(data[0] == 8)
    assert(data[1]<=0x1c)
    errflag = data[2]
    values = []
    for i in range(0, data[1]-1, 3):
        sd = data[2+1+i:2+1+i+3].hex()
        sv = int(sd[0:3], 16)
        sc = int(sd[3:6], 16)
        v = volt_adc_correct(sv, HVgain16, HVzero16)
        c = curr_adc_correct(sc, HCgain04, HCzero04)
        values.append((v, c))
    return (errflag, values)


# type 9
def genSetLedColor(idcode, led_color=0x3168, ch_on_m01=0, blink=True):
    d = '{}{:02x}{:02x}{:04x}'.format(idcode, ch_on_m01, 1 if blink else 0, led_color)
    packet = genGenericPacket(9, bytes.fromhex(d))
    return packet

def parseType9Resp(data):
    """
        Type 9 response have id and OUT_HVzero16/OUT_HVgain16/OUT_HCzero04/OUT_HCgain04
    """
    assert(data[0] == 9)
    assert(data[1] == 14)
    idcode = data[2:6].hex()
    unk1 = data[6]
    HVzero16 = int(data[7:9].hex(), 16)
    HVgain16 = int(data[9:11].hex(), 16)
    HCzero04 = int(data[11:13].hex(), 16)
    HCgain04 = int(data[13:15].hex(), 16)
    assert(data[15]==2)
    return (idcode, unk1, HVzero16, HVgain16, HCzero04, HCgain04)


def volt_adc_correct(value, gain, offset):
    return int((value*16-offset)*gain/100000.0)

def curr_adc_correct(value, gain, offset):
    return int((value*4-offset)*gain/100000.0*2)

class RecvError(Exception):
    pass
class ChecksumError(Exception):
    pass


class P906:
    """
    """
    def __init__(self, serial:serial.Serial, addr:int, channel:int, idcode:int = None, ch_on_m01:int=0, led_color:int=0x3168, retries=3, log_level=logging.INFO):
        self.serial = serial
        if idcode:
            self.idcode = '{:08x}'.format(idcode)
        else:
            self.idcode = None
        self.addr = '{:010x}'.format(addr)
        self.ch = channel
        self.ch_on_m01 = ch_on_m01
        self.led_color = led_color
        self.retries = retries
        self.logger = logging.getLogger('P906')
        self.logger.setLevel(log_level)
        assert(0<=self.ch<=78)
        self.status = {'HVzero16': None, 'HVgain16': None, 'HCzero04': None, 'HCgain04': None,
                       'Voltage': None, 'Current': None,
                       'InputVoltage': None, 'InputCurrent': None,
                       'ErrFlag': None}
        self.serial.read_until(b'Ready\r\n')

    def serwrite(self,data):
        if isinstance(data, str):
            self.serial.write(data.encode('latin1'))
        elif isinstance(data, bytes):
            self.serial.write(data)
        else:
            raise Exception()

    def configAdapter(self):
        self.serwrite(b'\r\nAT+TEST\r\n')
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+CFG=5,{},3,1,2,1,32\r\n'.format(self.ch))
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+RXADDR=1,{}\r\n'.format(self.addr))
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+TXADDR={}\r\n'.format(self.addr))
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+LISTEN=start\r\n'.format(self.addr))
        self.serial.read_until(b'OK\r\n')

    def getAdapterCfg(self):
        self.serwrite(b'\r\nAT+TEST\r\n')
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+CFG\r\n')
        d = self.serial.read_until(b'OK\r\n')
        return d

    def send(self, data:bytes):
        self.serwrite('AT+TX={}\r\n'.format(data.hex()))
        self.serial.read_until(b'OK\r\n')

    def recv(self, retries=None):
        _r = 0
        if not retries:
            retries = self.retries
        while _r < retries:
            try:
                rdata = self.serial.read_until(b'\r\n')
                p, d = rdata.split(b',')
                d = bytes.fromhex(d.decode('latin1'))
                d = d[:d[1]+3]
                if mdpChecksum(d[:-1]) != d[-1:]:
                    raise ChecksumError(d)
                p = int(p)
                self.logger.debug("recv from pipe {}: \n{}".format(p, hexdump(d, result='return')))
                return (p, d)
            except ValueError:
                _r += 1
                continue
            raise RecvError(rdata)

        raise RecvError(rdata)

    def sr(self, data):
        self.serial.flush()
        self.send(data)
        self.logger.debug("send: \n{}".format(hexdump(data, result='return')))
        p, d = self.recv()
        return (p, d)

    def getGainOffset(self):
        _, d = self.sr(genSetLedColor(self.idcode, self.led_color, self.ch_on_m01))
        idcode, unk1, HVzero16, HVgain16, HCzero04, HCgain04 = parseType9Resp(d)
        if self.idcode == idcode:
            self.status['HVzero16'] = HVzero16
            self.status['HVgain16'] = HVgain16
            self.status['HCzero04'] = HCzero04
            self.status['HCgain04'] = HCgain04
            return (HVzero16, HVgain16, HCzero04, HCgain04)
        return None

    def getSetValue(self):
        _, d = self.sr(genGet7(self.idcode, self.ch_on_m01))
        errflag, input_volt, input_curr, voltage, current, _ = parseType7Resp(d)
        self.status['ErrFlag'] = errflag
        self.status['InputVoltage'] = input_volt
        self.status['InputCurrent'] = input_curr
        self.status['Voltage'] = voltage
        self.status['Current'] = current
        return (errflag, input_volt, input_curr, voltage, current)

    def getRealtimeValue(self):
        try:
            _, d = self.sr(genGet8(self.idcode, self.ch_on_m01))
            errFlag, values = parseType8Resp(d, self.status['HVzero16'], self.status['HVgain16'], self.status['HCzero04'], self.status['HCgain04'])
            self.status['ErrFlag'] = errFlag
            return values
        except RecvError:
            return None

    def switch(self, on:bool):
        try:
            d = self.sr(genSwitch(self.idcode, on, self.ch_on_m01))
            if (d[1][0] == 7 and d[1][1] == 0x1c):
                errflag, input_volt, input_curr, voltage, current, _ = parseType7Resp(d[1])
                self.status['ErrFlag'] = errflag
                self.status['InputVoltage'] = input_volt
                self.status['InputCurrent'] = input_curr
                self.status['Voltage'] = voltage
                self.status['Current'] = current
            elif d[1][0] == 8:
                pass
            return True
        except RecvError:
            return None

    def setOutputVolt(self, voltage):
        try:
            d = self.sr(genSetVolt(self.idcode, voltage, self.ch_on_m01))
            if d[1][0] == 7:
                errflag, input_volt, input_curr, voltage, current, _ = parseType7Resp(d[1])
                self.status['ErrFlag'] = errflag
                self.status['InputVoltage'] = input_volt
                self.status['InputCurrent'] = input_curr
                self.status['Voltage'] = voltage
                self.status['Current'] = current
            elif d[1][0] == 8:
                pass
            return True
        except RecvError:
            return None

    def setOutputCurr(self, current):
        try:
            d = self.sr(genSetCurr(self.idcode, current, self.ch_on_m01))
            if d[1][0] == 7:
                errflag, input_volt, input_curr, voltage, current, _ = parseType7Resp(d[1])
                self.status['ErrFlag'] = errflag
                self.status['InputVoltage'] = input_volt
                self.status['InputCurrent'] = input_curr
                self.status['Voltage'] = voltage
                self.status['Current'] = current
            elif d[1][0] == 8:
                pass
            return True
        except RecvError:
            return None

    def connect(self):
        assert(self.idcode)
        assert(self.addr)
        assert(self.ch)
        self.configAdapter()
        self.getGainOffset()
        self.getSetValue()

    def autoMatch(self, retries=320):
        self.serwrite(b'\r\nAT+TEST\r\n')
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+CFG=5,{},3,1,2,1,32\r\n'.format(78))
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+RXADDR=1,{}\r\n'.format('FFFFFFFFFF'))
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+TXADDR={}\r\n'.format('FFFFFFFFFF'))
        self.serial.read_until(b'OK\r\n')
        self.serwrite('AT+LISTEN=start\r\n'.format(self.addr))
        self.serial.read_until(b'OK\r\n')
        while retries:
            try:
                self.logger.info('finding device')
                d = self.sr(genCallForId())
                self.idcode = parseType5Resp(d[1])
            except RecvError:
                retries -= 1
                continue
            break
        self.logger.info('sent Type 5 Call For Id on channel 78 to FFFFFFFFFF, recv id:{}'.format(self.idcode))
        d = self.sr(genDispatchChAddr(self.addr, self.ch))
        self.logger.info('sent Type 6 Dispatch channel {}, addr {}, recv :{}'.format(self.ch, self.addr, d[1].decode('latin1')))
        self.configAdapter()
        return self.idcode


def doAutoMatch(serial_device, addr, channel):
    s = serial.Serial(serial_device, 115200, 8, 'N', 1, timeout=0.5)
    p = P906(s, addr, channel)
    idcode = p.autoMatch()
    print('matching P906 {} on channel {}, set addr to {:010x}'.format(idcode, channel, addr))

def doGet(serial_device, addr, channel, idcode):
    s = serial.Serial(serial_device, 115200, 8, 'N', 1, timeout=0.5)
    p = P906(s, addr, channel, idcode)
    p.connect()
    adc_data = p.getRealtimeValue()
    print(p.status)
    print('recently adc data(corrected, in mV/mA): {}'.format(adc_data))

def doLivePlot(serial_device, addr, channel, idcode):
    """
        DO NOT USE!
         a useless toy because of lag and packet loss
    """
    import numpy as np
    import matplotlib.pyplot as plt
    import matplotlib.animation as animation

    x = []
    volts = []
    currs = []

    figure, (volt_ax, curr_ax) = plt.subplots(2,1, sharex=True)
    xlimit = 1000
    volt_ax.set_xlim((0, xlimit))
    volt_line, = volt_ax.plot(x, volts)
    volt_ax.set_ylim(0, 30)

    curr_line, = curr_ax.plot(x, currs)
    curr_ax.set_ylim(0, 10)

    s = serial.Serial(serial_device, 115200, 8, 'N', 1, timeout=0.5)
    p = P906(s, addr, channel, idcode)
    p.connect()

    def func_animate(frame_n, p, args):
        values = p.getRealtimeValue()
        if values:
            for value in values:
                args[0] += 1
                x.append(args[0])
                volts.append(value[0]/1000.0)
                currs.append(value[1]/1000.0)
            if args[0] >= xlimit:
                x.pop(0)
                volts.pop(0)
                currs.pop(0)
                volt_ax.set_xlim((args[0]-xlimit, args[0]))

        volt_line.set_data(x, volts)
        curr_line.set_data(x, currs)

        return volt_line,curr_line

    args = [0]
    ani = animation.FuncAnimation(figure, func_animate, fargs=(p, args), frames=None, interval=200)

    plt.show()

def doSet(serial_device, addr, channel, idcode, operation, value):
    if operation not in ('volt', 'voltage', 'curr', 'current', 'switch'):
        return None
    s = serial.Serial(serial_device, 115200, 8, 'N', 1, timeout=0.5)
    p = P906(s, addr, channel, idcode)
    p.connect()
    if operation in ('volt', 'voltage'):
        value = float(value)
        r = p.setOutputVolt(value)
    elif operation in ('curr', 'current'):
        value = float(value)
        r = p.setOutputCurr(value)
    elif operation == 'switch':
        if value == 'on':
            r = p.switch(True)
        elif value == 'off':
            r = p.switch(False)
        else:
            return None
    if r is True:
        return True
    return None


if __name__ == '__main__':
    parent_parser = argparse.ArgumentParser(add_help=False)
    parser = argparse.ArgumentParser(add_help=False)
    subparser = parser.add_subparsers(title='action')
    parent_parser.add_argument('-d', '--serial', required=True, type=str, help='serial device for nrf24_adapter, eg. "/dev/ttyS16"')
    parent_parser.add_argument('-a', '--addr', type=lambda x: int(x,16), default='153614fae1', help='5 byte nrf24 rx/tx addr in hex format')
    parent_parser.add_argument('-c', '--channel', type=int, default='50', help='nrf24 channel (0-78)')
    parent_parser.add_argument('-l', '--loglevel', type=str, default='INFO', help='log level')
    subparser.required = True
    subparser.dest = 'action'
    matchParser = subparser.add_parser('match', parents=[parent_parser])
    getParser = subparser.add_parser('get', parents=[parent_parser])
    getParser.add_argument('-I', '--idcode', required=True, type=lambda x: int(x,16), help="P906's ID in hex format")
    plotParser = subparser.add_parser('plot',parents=[parent_parser])
    plotParser.add_argument('-I', '--idcode', required=True, type=lambda x: int(x,16), help="P906's ID in hex format")
    setParser = subparser.add_parser('set',parents=[parent_parser])
    setParser.add_argument('-I', '--idcode', required=True, type=lambda x: int(x,16), help="P906's ID in hex format")
    setParser.add_argument('operation', type=str, help='')
    setParser.add_argument('value', type=str, help='')
    args = parser.parse_args()
    logging.basicConfig(level=args.loglevel)
    if args.action == 'match':
        doAutoMatch(args.serial, args.addr, args.channel)
    elif args.action == 'get':
        doGet(args.serial, args.addr, args.channel, args.idcode)
    elif args.action == 'plot':
        doLivePlot(args.serial, args.addr, args.channel, args.idcode)
    elif args.action == 'set':
        r = doSet(args.serial, args.addr, args.channel, args.idcode, args.operation, args.value)
        if r is None:
            setParser.print_help()
    else:
        parser.print_help()
