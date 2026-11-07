#include "belt_protocol.h"

namespace belt {

DecodeError decodeCommand(const uint8_t* data, size_t len, Command& out) {
  if (data == nullptr || len < kCommandHeaderSize) return DecodeError::TooShort;
  if (data[0] != kProtocolVersion) return DecodeError::BadVersion;
  if (data[1] > static_cast<uint8_t>(Mode::Fault)) return DecodeError::BadMode;

  const uint8_t count = data[5];
  if (count == 0 || count > kMaxMotors) return DecodeError::BadCount;
  if (len != kCommandHeaderSize + count) return DecodeError::LengthMismatch;

  Command cmd;
  cmd.mode = static_cast<Mode>(data[1]);
  cmd.seq = data[2];
  cmd.ttlMs = static_cast<uint16_t>(data[3] | (data[4] << 8));
  cmd.count = count;
  for (uint8_t i = 0; i < count; ++i) cmd.levels[i] = data[kCommandHeaderSize + i];

  out = cmd;
  return DecodeError::None;
}

const char* toString(DecodeError err) {
  switch (err) {
    case DecodeError::None: return "ok";
    case DecodeError::TooShort: return "too short";
    case DecodeError::BadVersion: return "bad version";
    case DecodeError::BadMode: return "bad mode";
    case DecodeError::BadCount: return "bad motor count";
    case DecodeError::LengthMismatch: return "length mismatch";
  }
  return "unknown";
}

}  // namespace belt
