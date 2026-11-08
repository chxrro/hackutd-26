#include "belt_status.h"

namespace belt {

namespace {
void put16(uint8_t* p, uint16_t v) {
  p[0] = static_cast<uint8_t>(v);
  p[1] = static_cast<uint8_t>(v >> 8);
}
void put32(uint8_t* p, uint32_t v) {
  for (int i = 0; i < 4; ++i) p[i] = static_cast<uint8_t>(v >> (8 * i));
}
}  // namespace

StatusMode statusMode(const HapticController& c) {
  switch (c.state()) {
    case HapticController::State::Idle:
      return StatusMode::Idle;
    case HapticController::State::Dormant:
      return StatusMode::Dormant;
    case HapticController::State::LinkLost:
      return c.lostByDisconnect() ? StatusMode::Disconnected : StatusMode::Expired;
    case HapticController::State::Active:
      switch (c.lastCommand().mode) {
        case Mode::Off: return StatusMode::Off;
        case Mode::Levels: return StatusMode::Levels;
        case Mode::Fault: return StatusMode::Fault;
      }
      break;
  }
  return StatusMode::Idle;
}

Status makeStatus(const HapticController& c, uint32_t nowMs, uint32_t buildId, uint8_t maxDuty) {
  Status s;
  s.mode = statusMode(c);
  s.uptimeMs = nowMs;
  s.buildId = buildId;
  s.maxDuty = maxDuty;
  if (c.hasCommand()) {
    s.seq = c.lastCommand().seq;
    const uint32_t age = static_cast<uint32_t>(nowMs - c.commandAtMs());
    s.commandAgeMs = age >= kStatusNoCommandAge ? kStatusNoCommandAge : static_cast<uint16_t>(age);
  }
  s.count = c.motorCount();
  for (uint8_t i = 0; i < s.count; ++i) s.levels[i] = c.lastLevels()[i];
  return s;
}

size_t encodeStatus(const Status& s, uint8_t* out, size_t cap) {
  const uint8_t count = s.count > kMaxMotors ? kMaxMotors : s.count;
  const size_t len = kStatusHeaderSize + count;
  if (out == nullptr || cap < len) return 0;
  out[0] = kStatusVersion;
  out[1] = static_cast<uint8_t>(s.mode);
  out[2] = s.seq;
  put16(out + 3, s.commandAgeMs);
  put32(out + 5, s.uptimeMs);
  put32(out + 9, s.buildId);
  out[13] = s.maxDuty;
  out[14] = count;
  for (uint8_t i = 0; i < count; ++i) out[kStatusHeaderSize + i] = s.levels[i];
  return len;
}

const char* toString(StatusMode mode) {
  switch (mode) {
    case StatusMode::Idle: return "idle";
    case StatusMode::Off: return "off";
    case StatusMode::Levels: return "levels";
    case StatusMode::Fault: return "fault";
    case StatusMode::Expired: return "expired";
    case StatusMode::Disconnected: return "disconnected";
    case StatusMode::Dormant: return "dormant";
  }
  return "unknown";
}

bool StatusNotifier::shouldNotify(StatusMode mode, uint32_t nowMs) {
  modeChanged_ = started_ && mode != last_;
  const bool due = !started_ || modeChanged_ || static_cast<uint32_t>(nowMs - lastAtMs_) >= periodMs_;
  if (!due) return false;
  started_ = true;
  last_ = mode;
  lastAtMs_ = nowMs;
  return true;
}

}  // namespace belt
