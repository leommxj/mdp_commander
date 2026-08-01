#include <RF24.h>
#include <printf.h>
#include <avr/wdt.h>
#include <string.h>

constexpr uint8_t CE_PIN = 7;
constexpr uint8_t CSN_PIN = 8;
constexpr uint8_t PAYLOAD_SIZE = 32;
constexpr uint8_t RETRY_DELAY = 0;  // 250 us
constexpr uint8_t RETRY_COUNT = 3;
constexpr size_t LINE_SIZE = 96;

RF24 radio(CE_PIN, CSN_PIN);
uint8_t rxBuffer[PAYLOAD_SIZE] = {};
uint8_t txBuffer[PAYLOAD_SIZE] = {};
char lineBuffer[LINE_SIZE] = {};
size_t lineLength = 0;
bool lineOverflow = false;
bool radioReady = false;
bool listening = false;

struct Counters {
  uint32_t txOk = 0;
  uint32_t txNoAck = 0;
  uint32_t rx = 0;
  uint32_t invalidCommand = 0;
} counters;

void disableWatchdog() __attribute__((naked, used, section(".init3")));
void disableWatchdog() {
  MCUSR = 0;
  wdt_disable();
}

void replyOk() {
  Serial.println(F("OK"));
}

void replyError(const __FlashStringHelper* reason) {
  Serial.print(F("ERROR,"));
  Serial.println(reason);
}

void rejectCommand(const __FlashStringHelper* reason) {
  ++counters.invalidCommand;
  replyError(reason);
}

void printHex(const uint8_t* data, size_t length) {
  for (size_t i = 0; i < length; ++i) {
    if (data[i] < 0x10) {
      Serial.print('0');
    }
    Serial.print(data[i], HEX);
  }
}

int8_t hexNibble(char value) {
  if (value >= '0' && value <= '9') {
    return value - '0';
  }
  if (value >= 'A' && value <= 'F') {
    return value - 'A' + 10;
  }
  if (value >= 'a' && value <= 'f') {
    return value - 'a' + 10;
  }
  return -1;
}

bool decodeHex(const char* text, uint8_t* output, size_t capacity,
               size_t& outputLength) {
  const size_t length = strlen(text);
  if (length == 0 || (length & 1) != 0 || length / 2 > capacity) {
    return false;
  }

  outputLength = length / 2;
  for (size_t i = 0; i < outputLength; ++i) {
    const int8_t high = hexNibble(text[i * 2]);
    const int8_t low = hexNibble(text[i * 2 + 1]);
    if (high < 0 || low < 0) {
      return false;
    }
    output[i] = static_cast<uint8_t>((high << 4) | low);
  }
  return true;
}

bool parseUint8(const char* text, uint8_t maximum, uint8_t& result) {
  if (*text == '\0') {
    return false;
  }

  uint16_t value = 0;
  while (*text != '\0') {
    if (*text < '0' || *text > '9') {
      return false;
    }
    value = value * 10 + static_cast<uint8_t>(*text++ - '0');
    if (value > maximum) {
      return false;
    }
  }
  result = static_cast<uint8_t>(value);
  return true;
}

bool splitCsv(char* text, char** fields, size_t fieldCount) {
  for (size_t i = 0; i < fieldCount; ++i) {
    if (*text == '\0') {
      return false;
    }
    fields[i] = text;
    char* comma = strchr(text, ',');
    if (i + 1 == fieldCount) {
      return comma == nullptr;
    }
    if (comma == nullptr) {
      return false;
    }
    *comma = '\0';
    text = comma + 1;
  }
  return false;
}

bool requireRadio() {
  if (radioReady) {
    return true;
  }
  replyError(F("RADIO_INIT"));
  return false;
}

void printInfo() {
  Serial.print(F("FW=2,RADIO="));
  Serial.print(radioReady ? 1 : 0);
  Serial.print(F(",LISTEN="));
  Serial.print(listening ? 1 : 0);
  Serial.print(F(",PAYLOAD="));
  Serial.println(PAYLOAD_SIZE);
}

void printStats() {
  Serial.print(F("TX_OK="));
  Serial.print(counters.txOk);
  Serial.print(F(",TX_NO_ACK="));
  Serial.print(counters.txNoAck);
  Serial.print(F(",RX="));
  Serial.print(counters.rx);
  Serial.print(F(",INVALID="));
  Serial.println(counters.invalidCommand);
}

void handleConfig(char* arguments) {
  if (!requireRadio()) {
    return;
  }

  char* fields[7];
  uint8_t values[7];
  constexpr uint8_t maxima[7] = {5, 125, 3, 2, 2, 1, PAYLOAD_SIZE};
  if (!splitCsv(arguments, fields, 7)) {
    rejectCommand(F("CFG"));
    return;
  }
  for (size_t i = 0; i < 7; ++i) {
    if (!parseUint8(fields[i], maxima[i], values[i])) {
      rejectCommand(F("CFG"));
      return;
    }
  }
  if (values[0] < 3 || values[6] != PAYLOAD_SIZE) {
    rejectCommand(F("CFG"));
    return;
  }

  const bool resumeListening = listening;
  if (resumeListening) {
    radio.stopListening();
  }
  radio.setAddressWidth(values[0]);
  radio.setChannel(values[1]);
  radio.setPALevel(values[2]);
  const bool dataRateSet =
      radio.setDataRate(static_cast<rf24_datarate_e>(values[3]));
  radio.setCRCLength(static_cast<rf24_crclength_e>(values[4]));
  radio.setAutoAck(values[5] != 0);
  radio.disableDynamicPayloads();
  radio.setPayloadSize(PAYLOAD_SIZE);
  radio.setRetries(RETRY_DELAY, RETRY_COUNT);
  if (resumeListening) {
    radio.startListening();
  }

  if (!dataRateSet) {
    replyError(F("RADIO_CONFIG"));
    return;
  }
  replyOk();
}

void handleRxAddress(char* arguments) {
  if (!requireRadio()) {
    return;
  }

  char* comma = strchr(arguments, ',');
  if (comma == nullptr) {
    rejectCommand(F("RXADDR"));
    return;
  }
  *comma = '\0';
  uint8_t pipe = 0;
  uint8_t addressInput[5];
  size_t addressLength = 0;
  if (!parseUint8(arguments, 5, pipe) ||
      !decodeHex(comma + 1, addressInput, sizeof(addressInput), addressLength) ||
      addressLength != sizeof(addressInput)) {
    rejectCommand(F("RXADDR"));
    return;
  }

  uint8_t address[5];
  for (size_t i = 0; i < sizeof(address); ++i) {
    address[i] = addressInput[sizeof(address) - i - 1];
  }
  const bool resumeListening = listening;
  if (resumeListening) {
    radio.stopListening();
  }
  radio.openReadingPipe(pipe, address);
  if (resumeListening) {
    radio.startListening();
  }
  replyOk();
}

void handleTxAddress(char* arguments) {
  if (!requireRadio()) {
    return;
  }

  uint8_t addressInput[5];
  size_t addressLength = 0;
  if (!decodeHex(arguments, addressInput, sizeof(addressInput), addressLength) ||
      addressLength != sizeof(addressInput)) {
    rejectCommand(F("TXADDR"));
    return;
  }

  uint8_t address[5];
  for (size_t i = 0; i < sizeof(address); ++i) {
    address[i] = addressInput[sizeof(address) - i - 1];
  }
  const bool resumeListening = listening;
  if (resumeListening) {
    radio.stopListening();
  }
  radio.openWritingPipe(address);
  if (resumeListening) {
    radio.startListening();
  }
  replyOk();
}

void handleListen(const char* argument) {
  if (!requireRadio()) {
    return;
  }
  if (strcasecmp(argument, "start") == 0) {
    radio.startListening();
    listening = true;
    replyOk();
    return;
  }
  if (strcasecmp(argument, "stop") == 0) {
    radio.stopListening();
    listening = false;
    replyOk();
    return;
  }
  rejectCommand(F("LISTEN"));
}

void handleTransmit(const char* argument) {
  if (!requireRadio()) {
    return;
  }

  memset(txBuffer, 0, sizeof(txBuffer));
  size_t dataLength = 0;
  if (!decodeHex(argument, txBuffer, sizeof(txBuffer), dataLength)) {
    rejectCommand(F("TX"));
    return;
  }

  const bool resumeListening = listening;
  if (resumeListening) {
    radio.stopListening();
  }
  const bool acknowledged = radio.write(txBuffer, sizeof(txBuffer));
  if (resumeListening) {
    radio.startListening();
  }

  if (acknowledged) {
    ++counters.txOk;
    replyOk();
  } else {
    ++counters.txNoAck;
    replyError(F("TX_NO_ACK"));
  }
}

bool startsWith(const char* text, const char* prefix) {
  return strncmp(text, prefix, strlen(prefix)) == 0;
}

void handleCommand(char* command) {
  if (strcmp(command, "AT+TEST") == 0) {
    if (requireRadio()) {
      replyOk();
    }
  } else if (strcmp(command, "AT+INFO") == 0) {
    printInfo();
    replyOk();
  } else if (strcmp(command, "AT+STAT") == 0) {
    printStats();
    replyOk();
  } else if (strcmp(command, "AT+CFG") == 0) {
    if (requireRadio()) {
      printInfo();
      radio.printDetails();
      replyOk();
    }
  } else if (startsWith(command, "AT+CFG=")) {
    handleConfig(command + 7);
  } else if (startsWith(command, "AT+RXADDR=")) {
    handleRxAddress(command + 10);
  } else if (startsWith(command, "AT+TXADDR=")) {
    handleTxAddress(command + 10);
  } else if (startsWith(command, "AT+LISTEN=")) {
    handleListen(command + 10);
  } else if (startsWith(command, "AT+TX=")) {
    handleTransmit(command + 6);
  } else if (strcmp(command, "AT+RESET") == 0) {
    replyOk();
    Serial.flush();
    wdt_enable(WDTO_15MS);
    while (true) {
    }
  } else {
    rejectCommand(F("UNKNOWN"));
  }
}

void pollSerial() {
  while (Serial.available() > 0) {
    const int input = Serial.read();
    if (input < 0) {
      return;
    }
    const char value = static_cast<char>(input);
    if (value == '\r' || value == '\n') {
      if (lineOverflow) {
        rejectCommand(F("LINE_TOO_LONG"));
      } else if (lineLength > 0) {
        lineBuffer[lineLength] = '\0';
        handleCommand(lineBuffer);
      }
      lineLength = 0;
      lineOverflow = false;
    } else if (!lineOverflow) {
      if (lineLength + 1 < sizeof(lineBuffer)) {
        lineBuffer[lineLength++] = value;
      } else {
        lineLength = 0;
        lineOverflow = true;
      }
    }
  }
}

void pollRadio() {
  if (!radioReady) {
    return;
  }

  uint8_t pipe = 0;
  while (radio.available(&pipe)) {
    radio.read(rxBuffer, sizeof(rxBuffer));
    ++counters.rx;
    Serial.print(pipe);
    Serial.print(F(","));
    printHex(rxBuffer, sizeof(rxBuffer));
    Serial.println();
  }
}

void setup() {
  Serial.begin(115200);
  printf_begin();
  radioReady = radio.begin();
  if (radioReady) {
    radio.disableDynamicPayloads();
    radio.setPayloadSize(PAYLOAD_SIZE);
    radio.setRetries(RETRY_DELAY, RETRY_COUNT);
    Serial.println(F("Ready"));
  } else {
    replyError(F("RADIO_INIT"));
  }
  wdt_enable(WDTO_2S);
}

void loop() {
  pollSerial();
  pollRadio();
  wdt_reset();
}
