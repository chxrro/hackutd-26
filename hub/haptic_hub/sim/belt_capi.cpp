// C ABI around the belt firmware's pure logic (esp32/lib/belt_core), so the
// simulator runs the *same* decoding and failsafe code the ESP32 runs.
// Built on demand by haptic_hub/sim/firmware.py; not part of the firmware build.
#include <stddef.h>
#include <stdint.h>

#include "belt_protocol.h"
#include "belt_status.h"
#include "haptic_controller.h"

namespace {
struct SimBelt {
  explicit SimBelt(uint8_t motors) : controller(motors) {}
  belt::HapticController controller;
  belt::Mode lastMode = belt::Mode::Off;
  belt::StatusNotifier notifier;
};
}  // namespace

extern "C" {

void* belt_new(uint8_t motors) { return new SimBelt(motors); }

void belt_free(void* handle) { delete static_cast<SimBelt*>(handle); }

// Same path as main.cpp handleEvent(Payload). Returns belt::DecodeError (0 = ok).
int belt_on_payload(void* handle, const uint8_t* data, size_t len, uint32_t now_ms) {
  auto* b = static_cast<SimBelt*>(handle);
  belt::Command cmd;
  const belt::DecodeError err = belt::decodeCommand(data, len, cmd);
  if (err != belt::DecodeError::None) return static_cast<int>(err);
  b->controller.onCommand(cmd, now_ms);
  b->lastMode = cmd.mode;
  return 0;
}

// Same path as main.cpp handleEvent(Disconnected).
void belt_on_link_lost(void* handle, uint32_t now_ms) {
  static_cast<SimBelt*>(handle)->controller.onLinkLost(now_ms);
}

// Same as one iteration of main.cpp loop(): writes motor levels, returns the state
// (0 idle, 1 active, 2 link-lost, 3 dormant).
int belt_update(void* handle, uint32_t now_ms, uint8_t* out_levels) {
  auto* b = static_cast<SimBelt*>(handle);
  b->controller.update(now_ms, out_levels);
  return static_cast<int>(b->controller.state());
}

// Mode of the last accepted command (0 off, 1 levels, 2 fault).
int belt_last_mode(void* handle) { return static_cast<int>(static_cast<SimBelt*>(handle)->lastMode); }

// Same as the status part of main.cpp loop(), after belt_update(): returns 1 if a
// status notification is due now (mode change or period), else 0.
int belt_status_due(void* handle, uint32_t now_ms) {
  auto* b = static_cast<SimBelt*>(handle);
  return b->notifier.shouldNotify(belt::statusMode(b->controller), now_ms) ? 1 : 0;
}

// Encodes the current status payload into out; returns its length (0 if cap is too small).
size_t belt_status(void* handle, uint32_t now_ms, uint32_t build_id, uint8_t max_duty, uint8_t* out, size_t cap) {
  auto* b = static_cast<SimBelt*>(handle);
  return belt::encodeStatus(belt::makeStatus(b->controller, now_ms, build_id, max_duty), out, cap);
}

uint8_t belt_motor_count(void* handle) { return static_cast<SimBelt*>(handle)->controller.motorCount(); }
}
