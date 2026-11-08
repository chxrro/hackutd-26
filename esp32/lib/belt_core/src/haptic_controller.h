// Decides what every motor should do at a given moment. Pure logic driven by an
// injected clock (milliseconds), so it can be unit-tested on the host.
//
// Safety rules (docs/protocol.md "Belt failsafe behavior"):
//  - every command expires after its (clamped) TTL;
//  - an expired command or a lost link stops the motors and plays the fault
//    pattern for a bounded time, so silence is never the result of a failure;
//  - before the first command after boot the belt stays silent.
#pragma once

#include <stdint.h>

#include "belt_protocol.h"

namespace belt {

struct HapticTiming {
  uint16_t minTtlMs = 50;
  uint16_t maxTtlMs = 1000;
  // Fault pattern: pulse, gap, pulse, then silence until the period ends.
  uint16_t faultPulseMs = 150;
  uint16_t faultGapMs = 150;
  uint16_t faultPeriodMs = 2000;
  uint8_t faultLevel = 180;
  // How long the belt keeps signalling a lost link before going quiet.
  uint32_t linkLostAlertMs = 30000;
};

class HapticController {
 public:
  enum class State : uint8_t {
    Idle,      // nothing received since boot: silent
    Active,    // following a valid, unexpired command
    LinkLost,  // command expired or BLE dropped: fault pattern (bounded)
    Dormant,   // link lost for longer than linkLostAlertMs: silent
  };

  explicit HapticController(uint8_t motorCount, HapticTiming timing = HapticTiming());

  void onCommand(const Command& cmd, uint32_t nowMs);
  void onLinkLost(uint32_t nowMs);

  // Back to Idle (silent) without signalling a fault. Only for the bench-test
  // console ending its own hold; never call it on a hub timeout.
  void reset();

  // Writes the level for every motor (motorCount entries) at time nowMs.
  void update(uint32_t nowMs, uint8_t* outLevels);

  State state() const { return state_; }
  uint8_t motorCount() const { return motorCount_; }

  // For the status characteristic (belt_status.h).
  bool hasCommand() const { return hasCommand_; }  // a command was applied since boot / reset()
  const Command& lastCommand() const { return current_; }
  uint32_t commandAtMs() const { return commandAtMs_; }
  bool lostByDisconnect() const { return lostByDisconnect_; }  // LinkLost/Dormant: BLE dropped, not TTL
  const uint8_t* lastLevels() const { return lastLevels_; }  // what the last update() wrote

 private:
  bool faultPatternOn(uint32_t elapsedMs) const;
  void enterLinkLost(uint32_t nowMs);

  uint8_t motorCount_;
  HapticTiming timing_;
  State state_ = State::Idle;
  Command current_;
  uint32_t commandAtMs_ = 0;
  uint16_t ttlMs_ = 0;
  uint32_t faultSinceMs_ = 0;
  bool hasCommand_ = false;
  bool lostByDisconnect_ = false;
  uint8_t lastLevels_[kMaxMotors] = {};
};

const char* toString(HapticController::State state);

}  // namespace belt
