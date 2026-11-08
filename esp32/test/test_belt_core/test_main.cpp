// Host-side tests for lib/belt_core:  pio test -e native
#include <belt_protocol.h>
#include <console.h>
#include <haptic_controller.h>
#include <string.h>
#include <unity.h>

using belt::Command;
using belt::DecodeError;
using belt::HapticController;
using belt::Mode;

namespace {

// Golden vector from docs/protocol.md: levels, seq=7, ttl=250, [0, 64, 128, 192, 255].
const uint8_t kGolden[] = {0x01, 0x01, 0x07, 0xfa, 0x00, 0x05, 0x00, 0x40, 0x80, 0xc0, 0xff};

Command levelsCommand(uint8_t a, uint16_t ttl = 250) {
  Command c;
  c.mode = Mode::Levels;
  c.ttlMs = ttl;
  c.count = 5;
  for (uint8_t i = 0; i < 5; ++i) c.levels[i] = a;
  return c;
}

Command modeCommand(Mode mode, uint16_t ttl = 250) {
  Command c = levelsCommand(0, ttl);
  c.mode = mode;
  return c;
}

bool allEqual(const uint8_t* levels, uint8_t n, uint8_t value) {
  for (uint8_t i = 0; i < n; ++i)
    if (levels[i] != value) return false;
  return true;
}

}  // namespace

void setUp() {}
void tearDown() {}

void test_decode_golden_vector() {
  Command c;
  TEST_ASSERT_EQUAL(DecodeError::None, belt::decodeCommand(kGolden, sizeof(kGolden), c));
  TEST_ASSERT_EQUAL(Mode::Levels, c.mode);
  TEST_ASSERT_EQUAL_UINT8(7, c.seq);
  TEST_ASSERT_EQUAL_UINT16(250, c.ttlMs);
  TEST_ASSERT_EQUAL_UINT8(5, c.count);
  const uint8_t expected[] = {0, 64, 128, 192, 255};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, c.levels, 5);
}

void test_decode_rejects_malformed() {
  Command c;
  TEST_ASSERT_EQUAL(DecodeError::TooShort, belt::decodeCommand(kGolden, 3, c));
  TEST_ASSERT_EQUAL(DecodeError::LengthMismatch, belt::decodeCommand(kGolden, sizeof(kGolden) - 1, c));

  uint8_t bad[sizeof(kGolden)];
  memcpy(bad, kGolden, sizeof(bad));
  bad[0] = 9;
  TEST_ASSERT_EQUAL(DecodeError::BadVersion, belt::decodeCommand(bad, sizeof(bad), c));
  bad[0] = 1;
  bad[1] = 7;
  TEST_ASSERT_EQUAL(DecodeError::BadMode, belt::decodeCommand(bad, sizeof(bad), c));
  bad[1] = 1;
  bad[5] = 0;
  TEST_ASSERT_EQUAL(DecodeError::BadCount, belt::decodeCommand(bad, sizeof(bad), c));
}

void test_silent_before_first_command() {
  HapticController hc(5);
  uint8_t out[5] = {1, 1, 1, 1, 1};
  hc.update(10000, out);
  TEST_ASSERT_TRUE(allEqual(out, 5, 0));
  TEST_ASSERT_EQUAL(HapticController::State::Idle, hc.state());
}

void test_levels_follow_command_until_ttl() {
  HapticController hc(5);
  uint8_t out[5];
  hc.onCommand(levelsCommand(200, 250), 1000);
  hc.update(1100, out);
  TEST_ASSERT_TRUE(allEqual(out, 5, 200));
  hc.update(1249, out);
  TEST_ASSERT_TRUE(allEqual(out, 5, 200));
}

void test_expired_command_plays_fault_then_goes_dormant() {
  belt::HapticTiming t;
  HapticController hc(5, t);
  uint8_t out[5];
  hc.onCommand(levelsCommand(200, 250), 1000);

  hc.update(1250, out);  // expired exactly at TTL: first fault pulse begins
  TEST_ASSERT_EQUAL(HapticController::State::LinkLost, hc.state());
  TEST_ASSERT_TRUE(allEqual(out, 5, t.faultLevel));

  hc.update(1250 + t.faultPulseMs + 10, out);  // in the gap between pulses
  TEST_ASSERT_TRUE(allEqual(out, 5, 0));

  hc.update(1250 + t.faultPulseMs + t.faultGapMs + 10, out);  // second pulse
  TEST_ASSERT_TRUE(allEqual(out, 5, t.faultLevel));

  hc.update(1250 + t.linkLostAlertMs, out);
  TEST_ASSERT_EQUAL(HapticController::State::Dormant, hc.state());
  TEST_ASSERT_TRUE(allEqual(out, 5, 0));
}

void test_ttl_is_clamped() {
  belt::HapticTiming t;
  HapticController hc(5, t);
  uint8_t out[5];
  hc.onCommand(levelsCommand(200, 60000), 0);  // asks for 60 s
  hc.update(t.maxTtlMs, out);
  TEST_ASSERT_EQUAL(HapticController::State::LinkLost, hc.state());
}

void test_disconnect_triggers_fault() {
  HapticController hc(5);
  uint8_t out[5];
  hc.onCommand(levelsCommand(200), 1000);
  hc.onLinkLost(1010);
  hc.update(1010, out);
  TEST_ASSERT_EQUAL(HapticController::State::LinkLost, hc.state());
  TEST_ASSERT_FALSE(allEqual(out, 5, 200));
}

void test_fault_mode_and_off_mode() {
  belt::HapticTiming t;
  HapticController hc(5, t);
  uint8_t out[5];
  hc.onCommand(modeCommand(Mode::Fault), 1000);
  hc.update(1000, out);
  TEST_ASSERT_TRUE(allEqual(out, 5, t.faultLevel));

  hc.onCommand(modeCommand(Mode::Off), 1100);
  hc.update(1100, out);
  TEST_ASSERT_TRUE(allEqual(out, 5, 0));
  TEST_ASSERT_EQUAL(HapticController::State::Active, hc.state());
}

void test_fewer_levels_than_motors_zero_fill() {
  HapticController hc(5);
  uint8_t out[5];
  Command c = levelsCommand(0);
  c.count = 2;
  c.levels[0] = 10;
  c.levels[1] = 20;
  c.levels[2] = 99;  // beyond count: must be ignored
  hc.onCommand(c, 0);
  hc.update(1, out);
  const uint8_t expected[] = {10, 20, 0, 0, 0};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, out, 5);
}

void test_millis_wraparound() {
  HapticController hc(5);
  uint8_t out[5];
  hc.onCommand(levelsCommand(100, 250), 0xFFFFFF00u);
  hc.update(0x00000010u, out);  // 0x110 = 272 ms later, past the TTL
  TEST_ASSERT_EQUAL(HapticController::State::LinkLost, hc.state());
}

void test_console_motor_command() {
  belt::ConsoleResult r = belt::parseConsoleLine("m 2 150", 5);
  TEST_ASSERT_EQUAL(belt::ConsoleAction::Hold, r.action);
  TEST_ASSERT_EQUAL(Mode::Levels, r.command.mode);
  const uint8_t expected[] = {0, 0, 150, 0, 0};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, r.command.levels, 5);
  TEST_ASSERT_EQUAL_UINT32(1000, r.holdMs);

  r = belt::parseConsoleLine("  M 4\r", 5);  // default level, case-insensitive, CR tolerated
  TEST_ASSERT_EQUAL(belt::ConsoleAction::Hold, r.action);
  TEST_ASSERT_EQUAL_UINT8(200, r.command.levels[4]);
}

void test_console_levels_fault_and_errors() {
  belt::ConsoleResult r = belt::parseConsoleLine("l 0 64 128 192 255", 5);
  TEST_ASSERT_EQUAL(belt::ConsoleAction::Hold, r.action);
  const uint8_t expected[] = {0, 64, 128, 192, 255};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, r.command.levels, 5);

  TEST_ASSERT_EQUAL(Mode::Fault, belt::parseConsoleLine("f", 5).command.mode);
  TEST_ASSERT_EQUAL(belt::ConsoleAction::SelfTest, belt::parseConsoleLine("t", 5).action);
  TEST_ASSERT_EQUAL(belt::ConsoleAction::None, belt::parseConsoleLine("", 5).action);
  TEST_ASSERT_EQUAL(belt::ConsoleAction::Invalid, belt::parseConsoleLine("m 5", 5).action);  // out of range
  TEST_ASSERT_EQUAL(belt::ConsoleAction::Invalid, belt::parseConsoleLine("m 1 300", 5).action);
  TEST_ASSERT_EQUAL(belt::ConsoleAction::Invalid, belt::parseConsoleLine("l 1 2 3", 5).action);  // too few
  TEST_ASSERT_EQUAL(belt::ConsoleAction::Invalid, belt::parseConsoleLine("x", 5).action);
}

void test_reset_returns_to_silent_idle() {
  HapticController hc(5);
  uint8_t out[5];
  hc.onCommand(levelsCommand(200), 0);
  hc.reset();
  hc.update(5000, out);  // long after the TTL: no fault, because reset != timeout
  TEST_ASSERT_EQUAL(HapticController::State::Idle, hc.state());
  TEST_ASSERT_TRUE(allEqual(out, 5, 0));
}

int main() {
  UNITY_BEGIN();
  RUN_TEST(test_decode_golden_vector);
  RUN_TEST(test_decode_rejects_malformed);
  RUN_TEST(test_silent_before_first_command);
  RUN_TEST(test_levels_follow_command_until_ttl);
  RUN_TEST(test_expired_command_plays_fault_then_goes_dormant);
  RUN_TEST(test_ttl_is_clamped);
  RUN_TEST(test_disconnect_triggers_fault);
  RUN_TEST(test_fault_mode_and_off_mode);
  RUN_TEST(test_fewer_levels_than_motors_zero_fill);
  RUN_TEST(test_millis_wraparound);
  RUN_TEST(test_console_motor_command);
  RUN_TEST(test_console_levels_fault_and_errors);
  RUN_TEST(test_reset_returns_to_silent_idle);
  return UNITY_END();
}
