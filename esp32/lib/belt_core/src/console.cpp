#include "console.h"

#include <ctype.h>
#include <stdlib.h>

namespace belt {

const char* const kConsoleHelp =
    "commands:\n"
    "  m <i> [level]   buzz motor i (0 = far left) for 1 s\n"
    "  l <a> <b> ...   set each motor's level 0-255 for 2 s\n"
    "  f               fault pattern for 4 s\n"
    "  t               self-test sweep\n"
    "  s               status\n";

namespace {

const char* skipSpaces(const char* p) {
  while (*p == ' ' || *p == '\t') ++p;
  return p;
}

// Parses an unsigned integer <= maxValue. Advances p on success.
bool parseUint(const char*& p, long maxValue, long& out) {
  p = skipSpaces(p);
  if (!isdigit(static_cast<unsigned char>(*p))) return false;
  char* end = nullptr;
  const long v = strtol(p, &end, 10);
  if (v < 0 || v > maxValue) return false;
  p = end;
  out = v;
  return true;
}

ConsoleResult invalid(const char* why) {
  ConsoleResult r;
  r.action = ConsoleAction::Invalid;
  r.error = why;
  return r;
}

ConsoleResult hold(const Command& cmd, uint32_t ms) {
  ConsoleResult r;
  r.action = ConsoleAction::Hold;
  r.command = cmd;
  r.holdMs = ms;
  return r;
}

}  // namespace

ConsoleResult parseConsoleLine(const char* line, uint8_t motorCount) {
  if (line == nullptr) return ConsoleResult();
  const char* p = skipSpaces(line);
  if (*p == '\0' || *p == '\r' || *p == '\n') return ConsoleResult();

  const char op = static_cast<char>(tolower(static_cast<unsigned char>(*p++)));
  Command cmd;
  cmd.count = motorCount;
  cmd.ttlMs = 1000;

  switch (op) {
    case 'm': {
      long index = 0, level = 200;
      if (!parseUint(p, motorCount - 1, index)) return invalid("usage: m <motor index> [level]");
      const char* rest = skipSpaces(p);
      if (*rest != '\0' && *rest != '\r' && *rest != '\n' && !parseUint(p, 255, level))
        return invalid("level must be 0-255");
      cmd.mode = Mode::Levels;
      cmd.levels[index] = static_cast<uint8_t>(level);
      return hold(cmd, 1000);
    }
    case 'l': {
      cmd.mode = Mode::Levels;
      for (uint8_t i = 0; i < motorCount; ++i) {
        long level = 0;
        if (!parseUint(p, 255, level)) return invalid("usage: l <level> x motor count, each 0-255");
        cmd.levels[i] = static_cast<uint8_t>(level);
      }
      return hold(cmd, 2000);
    }
    case 'f':
      cmd.mode = Mode::Fault;
      return hold(cmd, 4000);
    case 't': {
      ConsoleResult r;
      r.action = ConsoleAction::SelfTest;
      return r;
    }
    case 's': {
      ConsoleResult r;
      r.action = ConsoleAction::Status;
      return r;
    }
    case 'h':
    case '?': {
      ConsoleResult r;
      r.action = ConsoleAction::Help;
      return r;
    }
    default:
      return invalid("unknown command (h for help)");
  }
}

}  // namespace belt
