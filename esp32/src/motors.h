// PWM output to the vibration motors. Knows nothing about BLE or the protocol.
#pragma once

#include <stdint.h>

class Motors {
 public:
  Motors(const uint8_t* pins, uint8_t count, uint8_t maxDuty);

  void begin();
  void write(const uint8_t* levels);  // `count` levels, 0-255
  void allOff();
  void selfTest(uint16_t pulseMs, uint8_t level);  // blocking, call from setup()

 private:
  void writeOne(uint8_t index, uint8_t level);

  const uint8_t* pins_;
  uint8_t count_;
  uint8_t maxDuty_;
};
