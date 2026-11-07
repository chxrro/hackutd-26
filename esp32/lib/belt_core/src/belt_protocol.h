// Hub -> belt command decoding. Pure C++ (no Arduino) so it runs in native tests.
// Wire format: docs/protocol.md section 3.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace belt {

constexpr uint8_t kProtocolVersion = 1;
constexpr uint8_t kMaxMotors = 8;
constexpr size_t kCommandHeaderSize = 6;

enum class Mode : uint8_t {
  Off = 0,     // link alive, all motors off
  Levels = 1,  // drive motors at the given levels
  Fault = 2,   // hub can't vouch for its output: play the fault pattern
};

struct Command {
  Mode mode = Mode::Off;
  uint8_t seq = 0;
  uint16_t ttlMs = 0;
  uint8_t count = 0;
  uint8_t levels[kMaxMotors] = {};
};

enum class DecodeError : uint8_t {
  None,
  TooShort,
  BadVersion,
  BadMode,
  BadCount,
  LengthMismatch,
};

// Parses a command payload. On success returns DecodeError::None and fills `out`.
DecodeError decodeCommand(const uint8_t* data, size_t len, Command& out);

const char* toString(DecodeError err);

}  // namespace belt
