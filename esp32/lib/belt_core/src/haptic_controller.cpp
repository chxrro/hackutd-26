#include "haptic_controller.h"

namespace belt {

namespace {
uint16_t clampTtl(uint16_t ttl, const HapticTiming& t) {
  if (ttl < t.minTtlMs) return t.minTtlMs;
  if (ttl > t.maxTtlMs) return t.maxTtlMs;
  return ttl;
}
}  // namespace

HapticController::HapticController(uint8_t motorCount, HapticTiming timing)
    : motorCount_(motorCount > kMaxMotors ? kMaxMotors : motorCount), timing_(timing) {}

void HapticController::onCommand(const Command& cmd, uint32_t nowMs) {
  // Keep the fault pattern's phase continuous if we were already signalling a fault.
  const bool wasFault =
      state_ == State::LinkLost || (state_ == State::Active && current_.mode == Mode::Fault);
  if (cmd.mode == Mode::Fault && !wasFault) faultSinceMs_ = nowMs;

  current_ = cmd;
  hasCommand_ = true;
  lostByDisconnect_ = false;
  commandAtMs_ = nowMs;
  ttlMs_ = clampTtl(cmd.ttlMs, timing_);
  state_ = State::Active;
}

void HapticController::onLinkLost(uint32_t nowMs) {
  if (state_ == State::Active) {
    enterLinkLost(nowMs);
    lostByDisconnect_ = true;
  }
}

void HapticController::reset() {
  state_ = State::Idle;
  current_ = Command();
  hasCommand_ = false;
  lostByDisconnect_ = false;
}

void HapticController::enterLinkLost(uint32_t nowMs) {
  state_ = State::LinkLost;
  faultSinceMs_ = nowMs;
}

bool HapticController::faultPatternOn(uint32_t elapsedMs) const {
  const uint32_t phase = elapsedMs % timing_.faultPeriodMs;
  const uint32_t p = timing_.faultPulseMs;
  const uint32_t g = timing_.faultGapMs;
  return phase < p || (phase >= p + g && phase < 2 * p + g);
}

void HapticController::update(uint32_t nowMs, uint8_t* outLevels) {
  if (state_ == State::Active && static_cast<uint32_t>(nowMs - commandAtMs_) >= ttlMs_) {
    enterLinkLost(commandAtMs_ + ttlMs_);
  }
  if (state_ == State::LinkLost &&
      static_cast<uint32_t>(nowMs - faultSinceMs_) >= timing_.linkLostAlertMs) {
    state_ = State::Dormant;
  }

  bool fault = false;
  switch (state_) {
    case State::Idle:
    case State::Dormant:
      break;
    case State::LinkLost:
      fault = true;
      break;
    case State::Active:
      if (current_.mode == Mode::Fault) {
        fault = true;
      } else if (current_.mode == Mode::Levels) {
        for (uint8_t i = 0; i < motorCount_; ++i) {
          outLevels[i] = i < current_.count ? current_.levels[i] : 0;
          lastLevels_[i] = outLevels[i];
        }
        return;
      }
      break;
  }

  const uint8_t level =
      fault && faultPatternOn(static_cast<uint32_t>(nowMs - faultSinceMs_)) ? timing_.faultLevel : 0;
  for (uint8_t i = 0; i < motorCount_; ++i) outLevels[i] = lastLevels_[i] = level;
}

const char* toString(HapticController::State state) {
  switch (state) {
    case HapticController::State::Idle: return "idle";
    case HapticController::State::Active: return "active";
    case HapticController::State::LinkLost: return "link-lost";
    case HapticController::State::Dormant: return "dormant";
  }
  return "unknown";
}

}  // namespace belt
