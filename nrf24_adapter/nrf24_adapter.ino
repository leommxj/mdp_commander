#include <ATCommands.h>
#include <RF24.h>
#include <RF24_config.h>
#include <nRF24L01.h>
#include <printf.h>
#include <avr/wdt.h>

RF24 radio(7, 8);


#define SERBUF_SIZE 255
#define RXBUF_SIZE 32
#define TXBUF_SIZE 32
byte rxbuf[RXBUF_SIZE] = {0};
byte txbuf[TXBUF_SIZE] = {0};
ATCommands AT;
uint8_t curPipeNum;



void hexprint(byte* data, size_t len) {
  for (int i = 0; i < len; i++) {
    if(data[i]<16){
      Serial.print("0");
    }
    Serial.print(data[i], HEX);
  }
}
void unhex(const char* str, byte* buf, size_t len) {
  for (; len > 1; len -= 2) {
    byte val = asc2byte(*str++) << 4;
    *buf++ = val | asc2byte(*str++);
  }
}
void unhex_reverse(const char* str, byte* buf, size_t len){
    for (; len > 1; len -= 2) {
    byte val = asc2byte(*str++) << 4;
    buf[(len/2)-1] = val | asc2byte(*str++);
  }
}

byte asc2byte(char chr) {
  byte rVal = 0;
  if (isdigit(chr)) {
    rVal = chr - '0';
  } else if (chr >= 'A' && chr <= 'F') {
    rVal = chr + 10 - 'A';
  }else if (chr >='a' && chr <= 'f'){
    rVal = chr + 10 - 'a';
  }
  return rVal;
}

bool at_test_print(ATCommands *sender){
  return true;
}

bool at_rxaddr_print(ATCommands *sender) {
  sender->serial->println("fucking help");
  return false;
}
/*
  AT+RXADDR=1,153614fae1
*/
bool at_rxaddr_data(ATCommands *sender) {
  byte addr_buf[5] = {0};
  int pipe = sender->next().toInt();
  String rxaddr_str = sender->next();
  unhex_reverse(rxaddr_str.c_str(), addr_buf, 10);
  radio.openReadingPipe(pipe, addr_buf);
  return true;
}
bool at_txaddr_print(ATCommands *sender) {
  return false;
}
/*
  AT+TXADDR=153614fae1
*/
bool at_txaddr_data(ATCommands *sender) {
  byte addr_buf[5] = {0};
  String rxaddr_str = sender->next();
  unhex_reverse(rxaddr_str.c_str(), addr_buf, 10);
  radio.openWritingPipe(addr_buf);
  return true;
}

bool at_cfg_print(ATCommands *sender) {
  radio.printDetails();
  return true;
}
/*
  AT+CFG=5,50,3,1,2,1,32
*/
bool at_cfg_data(ATCommands *sender) {
  String addressWidth = sender->next(); 
  String channel = sender->next();
  String paLevel = sender->next();
  String dataRate = sender->next();
  String crcLength = sender->next();
  String autoAck = sender->next();
  int payloadSize= sender->next().toInt();

  radio.setAddressWidth(addressWidth.toInt());
  radio.setChannel(channel.toInt());
  radio.setPALevel(paLevel.toInt());
  radio.setDataRate(dataRate.toInt());
  radio.setCRCLength(crcLength.toInt());
  radio.setAutoAck(autoAck.toInt());
  if (payloadSize == 0){
    radio.enableDynamicPayloads();
  }else{
    radio.disableDynamicPayloads();
    radio.setPayloadSize(payloadSize);
  }
  return true;
}

bool at_listen_print(ATCommands *sender) {
  return false;
}
/*
  AT+LISTEN=start
*/
bool at_listen_data(ATCommands *sender) {
  String arg = sender->next();
  if (arg.equalsIgnoreCase(String("start"))){
    radio.startListening();
  }else if(arg.equalsIgnoreCase(String("stop"))){
    radio.stopListening();
  }else{
    sender->serial->println("unknown op");
    return false;
  }
  return true;
}

bool at_tx_print(ATCommands *sender) {
  return true;
}
/*
  AT+TX=AABBCC112233
*/
bool at_tx_data(ATCommands *sender){
  memset(txbuf, 0, 32);
  String txdata = sender->next();
  if (txdata.length()/2 >= TXBUF_SIZE )
    return false;
  unhex(txdata.c_str(), txbuf, txdata.length());
  hexprint(txbuf, 32);
  radio.stopListening();
  radio.write(txbuf, radio.getPayloadSize());
  radio.startListening();
  return true;
}

bool at_reset(){
  wdt_enable(WDTO_15MS); // resets the MCU after 15 milliseconds
  while(true);
}

static at_command_t at_commands[] = {
  { "+RXADDR", at_rxaddr_print, NULL, NULL, at_rxaddr_data },
  { "+CFG", at_cfg_print, NULL, NULL, at_cfg_data },
  { "+TXADDR", at_txaddr_print, NULL, NULL, at_txaddr_data },
  { "+LISTEN", at_listen_print, NULL, NULL, at_listen_data },
  { "+TX", at_tx_print, NULL, NULL, at_tx_data},
  { "+RESET", at_reset, NULL, NULL, NULL},
  { "+TEST", at_test_print, NULL, NULL, NULL},
};


void handle_radio_data(uint8_t pipeNum, char* data, size_t len){
  Serial.print(pipeNum);
  Serial.print(F(","));
  hexprint(data, len);
  Serial.println("");
}

void setup() {
  Serial.begin(115200);
  printf_begin();
  AT.begin(&Serial, at_commands, sizeof(at_commands), SERBUF_SIZE);
  radio.begin();
  Serial.println(F("Ready"));
}
void loop() {
  AT.update();

  if (radio.available(&curPipeNum)) {
    int payloadSize = radio.getPayloadSize();
    radio.read(&rxbuf, payloadSize);
    handle_radio_data(curPipeNum,rxbuf, payloadSize);
  }
}
