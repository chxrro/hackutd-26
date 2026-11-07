#include "motors.h"

#include <Arduino.h>

#include "config.h"

Motors::Motors(const uint8_t* pins, uint8_t count, uint8_t maxDuty)
    : pins_(pins), count_(count), maxDuty_(maxDuty) {}

void Motors::begin() {
  for (uint8_t i = 0; i < count_; ++i) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcAttach(pins_[i], cfg::kPwmFrequencyHz, cfg::kPwmResolutionBits);
#else
    ledcSetup(i, cfg::kPwmFrequencyHz, cfg::kPwmResolutionBits);  // channel i drives motor i
    ledcAttachPin(pins_[i], i);
#endif
  }
  allOff();
}

void Motors::writeOne(uint8_t index, uint8_t level) {
  const uint32_t duty = level > maxDuty_ ? maxDuty_ : level;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(pins_[index], duty);
#else
  ledcWrite(index, duty);
#endif
}

void Motors::write(const uint8_t* levels) {
  for (uint8_t i = 0; i < count_; ++i) writeOne(i, levels[i]);
}

void Motors::allOff() {
  for (uint8_t i = 0; i < count_; ++i) writeOne(i, 0);
}

void Motors::selfTest(uint16_t pulseMs, uint8_t level) {
  for (uint8_t i = 0; i < count_; ++i) {
    writeOne(i, level);
    delay(pulseMs);
    writeOne(i, 0);
    delay(pulseMs / 2);
  }
}
