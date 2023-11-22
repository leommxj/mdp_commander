# mdp_commander
a tool using `arduino` board and `nrf24l01` as adapter to control the `Miniware MDP-P906` power supply.
MDP-P906 is a Digital Power Supply Laboratory Programmable Linear Power Supply. It works great by itself. But during my use, I want to control it using a script on PC rather than the MDP-M01 module.  
The MDP-M01 and MDP-P906 are using nrf24 to communicate with each other, so with nrf24 we should be able to communicate with the power supply from PC. After some reverse engineering and coding, here it is.
[![demo](https://img.youtube.com/vi/77mbpnwBl-0/0.jpg)](https://www.youtube.com/watch?v=77mbpnwBl-0)
## Usage

### First

You need one arduino-compatible board and one nrf24l01 module. I'm using [emakefun/rf-nano: emakefun arduino nano V3.0 + nrf24L01+ (github.com)](https://github.com/emakefun/rf-nano) which *integrating the nrf24L01+ wireless chip based on the official standard Arduino Nano V3.0 motherboard* . Compile the code  in `nrf24_adapter` directory and Flash it. This adapter will receive AT command on serial.  

### Then

* using as a lib

```python
from mdp import P906
from serial import Serial
s = Serial('/dev/ttyS16', 115200, 8, 'N', 1, timeout=0.5)
p = P906(s, 0x153614fae1, 45, 0x62E6491B)
p.connect()
# or trigger match without machine id
p = P906(s, 0x153614fae1, 45)
p.autoMatch()

# set Voltage
p.setOutputVolt(12.0)
# set Current
p.setOutputCurr(5.0)
# switch on/off
p.switch(True)
p.switch(False)
# get current voltage and current, this return multiple value.
p.getRealtimeValue()
# and other features, read the code
```

* using as a CLI program

```shell
python3 mdp.py get -d /dev/ttyS16 -I ${MACHINEID}
{'HVzero16': 74, 'HVgain16': 48545, 'HCzero04': 14, 'HCgain04': 30765, 'Voltage': 19.0, 'Current': 5.0, 'InputVoltage': 25.208, 'InputCurrent': 0.29, 'ErrFlag': 0}
recently adc data(corrected, in mV/mA): [(19001, 77), (19001, 77), (19001, 84), (19001, 104), (19001, 89), (19001, 79), (19001, 77), (19001, 77), (19001, 107)]

python3 mdp.py set switch off -d /dev/ttyS16 -I ${MACHINEID}
python3 mdp.py set voltage 3.3 -d /dev/ttyS16 -I ${MACHINEID}
python3 mdp.py set curr 4 -d /dev/ttyS16 -I ${MACHINEID}
python3 mdp.py plot -d /dev/ttyS16 -I ${MACHINEID}
```



## Notice

* This are all based on reverse engineering. **NOT** well tested, It may **damage** your power supply. **Use it at your own risk**.
* Protocol are not complex
* P906 is very slow response when you lower the Voltage. It will not be immediately drop to the set value.
* The communication distance is very short, the nrf24 module should be really close to P906. (Code in P906 are using max PALevel already, but signal are too weak, It may be caused by the metal casing and heat sink)
