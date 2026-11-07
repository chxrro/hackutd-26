// HapticBelt firmware: receives haptic commands from the hub over BLE and drives
// the vibration motors. Wiring lives in include/config.h, the BLE contract in
// docs/protocol.md, and all timing/safety decisions in lib/belt_core.

#include <Arduino.h>
#include <belt_protocol.h>
#include <console.h>
#include <haptic_controller.h>

#include "ble_link.h"
#include "config.h"
#include "motors.h"

namespace {

Motors motors(cfg::kMotorPins, cfg::kMotorCount, cfg::kMaxDuty);
BleLink bleLink;
belt::HapticController controller(cfg::kMotorCount);

belt::HapticController::State lastState = belt::HapticController::State::Idle;
bool hubConnected = false;

// Serial bench-test console (see lib/belt_core/src/console.h).
char consoleLine[64];
size_t consoleLength = 0;
bool consoleHolding = false;
belt::Command consoleCommand;
uint32_t consoleHoldUntilMs = 0;

void runConsoleLine(const char* line, uint32_t nowMs) {
  const belt::ConsoleResult r = belt::parseConsoleLine(line, cfg::kMotorCount);
  switch (r.action) {
    case belt::ConsoleAction::None:
      return;
    case belt::ConsoleAction::Invalid:
      Serial.printf("? %s\n", r.error);
      return;
    case belt::ConsoleAction::Help:
      Serial.print(belt::kConsoleHelp);
      return;
    case belt::ConsoleAction::Status:
      Serial.printf("state=%s hub=%s motors=%u\n", belt::toString(controller.state()),
                    hubConnected ? "connected" : "not connected", cfg::kMotorCount);
      return;
    case belt::ConsoleAction::SelfTest:
    case belt::ConsoleAction::Hold:
      if (hubConnected) {
        Serial.println("? hub is connected; disconnect it to use the console");
        return;
      }
      if (r.action == belt::ConsoleAction::SelfTest) {
        motors.selfTest(cfg::kSelfTestPulseMs, cfg::kSelfTestLevel);
        return;
      }
      consoleCommand = r.command;
      consoleHoldUntilMs = nowMs + r.holdMs;
      consoleHolding = true;
      Serial.println("ok");
      return;
  }
}

void pollConsole(uint32_t nowMs) {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      consoleLine[consoleLength] = '\0';
      runConsoleLine(consoleLine, nowMs);
      consoleLength = 0;
    } else if (consoleLength < sizeof(consoleLine) - 1) {
      consoleLine[consoleLength++] = c;
    }
  }
  if (!consoleHolding) return;
  if (hubConnected || static_cast<int32_t>(nowMs - consoleHoldUntilMs) >= 0) {
    consoleHolding = false;
    if (!hubConnected) controller.reset();  // a finished bench test is not a fault
    return;
  }
  controller.onCommand(consoleCommand, nowMs);  // refresh so the TTL never lapses mid-hold
}

void handleEvent(const BleLink::Event& ev, uint32_t nowMs) {
  switch (ev.type) {
    case BleLink::Event::Type::Connected:
      Serial.println("[ble] hub connected");
      hubConnected = true;
      break;
    case BleLink::Event::Type::Disconnected:
      Serial.println("[ble] hub disconnected");
      hubConnected = false;
      controller.onLinkLost(nowMs);
      break;
    case BleLink::Event::Type::Payload: {
      belt::Command cmd;
      const belt::DecodeError err = belt::decodeCommand(ev.data, ev.length, cmd);
      if (err != belt::DecodeError::None) {
        Serial.printf("[ble] rejected command (%s, %u bytes)\n", belt::toString(err), ev.length);
        return;
      }
      controller.onCommand(cmd, nowMs);
      break;
    }
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  motors.begin();
  Serial.printf("[belt] %u motors, self-test\n", cfg::kMotorCount);
  motors.selfTest(cfg::kSelfTestPulseMs, cfg::kSelfTestLevel);

  if (!bleLink.begin(cfg::kMotorCount)) {
    Serial.println("[ble] failed to start; motors stay off");
    return;
  }
  Serial.printf("[ble] advertising as \"%s\"\n", cfg::kDeviceName);
  Serial.println("[console] type h for bench-test commands");
}

void loop() {
  const uint32_t now = millis();

  BleLink::Event ev;
  while (bleLink.poll(ev)) handleEvent(ev, now);
  pollConsole(now);

  uint8_t levels[belt::kMaxMotors] = {};
  controller.update(now, levels);
  motors.write(levels);

  if (controller.state() != lastState) {
    lastState = controller.state();
    Serial.printf("[belt] state -> %s\n", belt::toString(lastState));
  }

  delay(5);
}
