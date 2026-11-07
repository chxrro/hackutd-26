// Serial bench-test console: drive the motors from `pio device monitor` without
// the hub. Pure parsing, so it's covered by the native tests.
//
//   m <i> [level]        buzz motor i (0 = far left) for 1 s
//   l <a> <b> ...        set every motor's level (0-255) for 2 s
//   f                    play the fault pattern for 4 s
//   t                    self-test sweep
//   s                    print state
//   h                    help
#pragma once

#include <stdint.h>

#include "belt_protocol.h"

namespace belt {

enum class ConsoleAction : uint8_t { None, Hold, SelfTest, Status, Help, Invalid };

struct ConsoleResult {
  ConsoleAction action = ConsoleAction::None;
  Command command;          // valid for Hold
  uint32_t holdMs = 0;      // valid for Hold
  const char* error = "";   // valid for Invalid
};

ConsoleResult parseConsoleLine(const char* line, uint8_t motorCount);

extern const char* const kConsoleHelp;

}  // namespace belt
