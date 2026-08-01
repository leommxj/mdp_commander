#!/usr/bin/env python
"""Backward-compatible entry point for mdp-commander."""

from mdp_commander import (
    AdapterError,
    ChecksumError,
    MdpError,
    P906,
    PairingError,
    ProtocolError,
    RecvError,
)
from mdp_commander.cli import (
    doAdapterInfo,
    doAutoMatch,
    doGet,
    doLivePlot,
    doSet,
    main,
)
from mdp_commander.protocol import (
    curr_adc_correct,
    genCallForId,
    genDispatchChAddr,
    genGenericPacket,
    genGet7,
    genGet8,
    genGetVoltCurr,
    genSetCurr,
    genSetLedColor,
    genSetVolt,
    genSwitch,
    mdpChecksum,
    parseType4Resp,
    parseType5Resp,
    parseType6Resp,
    parseType7Resp,
    parseType8Resp,
    parseType9Resp,
    validatePacket,
    volt_adc_correct,
)


if __name__ == "__main__":
    raise SystemExit(main())
