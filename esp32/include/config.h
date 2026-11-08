// Board wiring and BLE identity. Change pins here to match your build.
#pragma once

#include <stdint.h>

namespace cfg {

// One GPIO per motor, ordered from the wearer's far LEFT to far RIGHT.
// Each pin drives one input of a ULN2803 (or the gate of a logic-level MOSFET).
// These ESP32-DevKit pins avoid strapping pins (0, 2, 12, 15) and input-only pins (34-39).
constexpr uint8_t kMotorPins[] = {25, 26, 27, 32, 33};
constexpr uint8_t kMotorCount = sizeof(kMotorPins) / sizeof(kMotorPins[0]);

constexpr uint32_t kPwmFrequencyHz = 5000;
constexpr uint8_t kPwmResolutionBits = 8;  // levels 0-255 map straight to duty

// Hard cap on duty (0-255), protecting motors rated below the supply voltage.
// Reported to the hub in the status characteristic.
constexpr uint8_t kMaxDuty = 255;

// Must match docs/protocol.md and hub/haptic_hub/protocol.py.
constexpr const char* kDeviceName = "HapticBelt";
constexpr const char* kServiceUuid = "88368a33-70aa-41e2-b071-4f618af1856a";
constexpr const char* kCommandUuid = "71dbf1b6-d7d2-4208-954b-b8a38e54618f";
constexpr const char* kInfoUuid = "986b42ae-86e7-4eb6-a579-cd8360e40c0b";
constexpr const char* kStatusUuid = "ba2928d9-3014-49de-8949-bd9933b65d7a";  // read + notify

// Power-on self-test: each motor buzzes briefly, left to right.
constexpr uint16_t kSelfTestPulseMs = 120;
constexpr uint8_t kSelfTestLevel = 160;

}  // namespace cfg
