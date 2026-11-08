// Belt -> hub status report (BLE read + notify). Pure C++ (no Arduino) so it runs
// in native tests and in the hub's simulator. Wire format: docs/protocol.md
// section 3, "Status".
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "belt_protocol.h"
#include "haptic_controller.h"

namespace belt {

constexpr uint8_t kStatusVersion = 1;
constexpr size_t kStatusHeaderSize = 15;
constexpr size_t kStatusMaxSize = kStatusHeaderSize + kMaxMotors;
// Periodic notify interval. The contract promises at most 500 ms between notifications.
constexpr uint32_t kStatusPeriodMs = 250;
constexpr uint16_t kStatusNoCommandAge = 0xFFFF;  // command_age_ms: none since boot, or >= 65.535 s

// What the belt is doing right now, as reported to the hub.
enum class StatusMode : uint8_t {
  Idle = 0,          // no command since boot: silent
  Off = 1,           // following an `off` command
  Levels = 2,        // following a `levels` command (active)
  Fault = 3,         // following a `fault` command: fault pattern
  Expired = 4,       // command TTL lapsed: fault pattern
  Disconnected = 5,  // BLE dropped while active: fault pattern
  Dormant = 6,       // fault pattern ran its 30 s and stopped: silent
};

struct Status {
  StatusMode mode = StatusMode::Idle;
  uint8_t seq = 0;                             // seq of the last applied command (0 if none)
  uint16_t commandAgeMs = kStatusNoCommandAge;  // ms since that command was applied, saturating
  uint32_t uptimeMs = 0;
  uint32_t buildId = 0;  // first 8 hex digits of the firmware's git commit, 0 = unknown
  uint8_t maxDuty = 255;
  uint8_t count = 0;
  uint8_t levels[kMaxMotors] = {};  // controller output 0-255; PWM duty = min(level, maxDuty)
};

StatusMode statusMode(const HapticController& controller);

// Snapshot after controller.update(nowMs, ...) for this loop iteration.
Status makeStatus(const HapticController& controller, uint32_t nowMs, uint32_t buildId, uint8_t maxDuty);

// Returns the number of bytes written (kStatusHeaderSize + count), or 0 if `cap` is too small.
size_t encodeStatus(const Status& status, uint8_t* out, size_t cap);

const char* toString(StatusMode mode);

// Decides when to send a status notification: on every mode change, and at
// least every periodMs otherwise.
class StatusNotifier {
 public:
  explicit StatusNotifier(uint32_t periodMs = kStatusPeriodMs) : periodMs_(periodMs) {}

  // Call once per loop iteration. True means "notify now" (and records it as sent).
  bool shouldNotify(StatusMode mode, uint32_t nowMs);

  // True if the last shouldNotify() call fired because the mode changed.
  bool modeChanged() const { return modeChanged_; }

 private:
  uint32_t periodMs_;
  bool started_ = false;
  bool modeChanged_ = false;
  StatusMode last_ = StatusMode::Idle;
  uint32_t lastAtMs_ = 0;
};

}  // namespace belt
