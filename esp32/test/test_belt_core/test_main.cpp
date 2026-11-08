// Host-side tests for lib/belt_core:  pio test -e native
#include <belt_protocol.h>
#include <belt_status.h>
#include <console.h>
#include <haptic_controller.h>
#include <string.h>
#include <unity.h>

using belt::Command;
using belt::DecodeError;
using belt::HapticController;
using belt::Mode;
using belt::StatusMode;

namespace {

// Golden vector from docs/protocol.md: levels, seq=7, ttl=250, [0, 64, 128, 192, 255].
const uint8_t kGolden[] = {0x01, 0x01, 0x07, 0xfa, 0x00, 0x05, 0x00, 0x40, 0x80, 0xc0, 0xff};

// Status golden vector from docs/protocol.md: mode levels, seq=7, command_age_ms=40,
// uptime_ms=123456, build_id=0x1a2b3c4d, max_duty=255, levels [0, 64, 128, 192, 255].
const uint8_t kStatusGolden[] = {0x01, 0x02, 0x07, 0x28, 0x00, 0x40, 0xe2, 0x01, 0x00, 0x4d,
                                 0x3c, 0x2b, 0x1a, 0xff, 0x05, 0x00, 0x40, 0x80, 0xc0, 0xff};

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

void test_status_golden_vector() {
  belt::Status s;
  s.mode = StatusMode::Levels;
  s.seq = 7;
  s.commandAgeMs = 40;
  s.uptimeMs = 123456;
  s.buildId = 0x1a2b3c4du;
  s.maxDuty = 255;
  s.count = 5;
  const uint8_t levels[] = {0, 64, 128, 192, 255};
  memcpy(s.levels, levels, 5);
  uint8_t out[belt::kStatusMaxSize];
  TEST_ASSERT_EQUAL_UINT32(sizeof(kStatusGolden), belt::encodeStatus(s, out, sizeof(out)));
  TEST_ASSERT_EQUAL_UINT8_ARRAY(kStatusGolden, out, sizeof(kStatusGolden));
  // 5 motors fit the 20-byte payload of the default BLE ATT MTU.
  TEST_ASSERT_EQUAL_UINT32(20, sizeof(kStatusGolden));
  TEST_ASSERT_EQUAL_UINT32(0, belt::encodeStatus(s, out, sizeof(kStatusGolden) - 1));  // too small
}

void test_status_built_from_the_controller() {
  belt::HapticTiming t;
  HapticController hc(5, t);
  uint8_t out[5];
  hc.update(100, out);
  belt::Status s = belt::makeStatus(hc, 100, 0xdeadbeefu, 190);
  TEST_ASSERT_EQUAL(StatusMode::Idle, s.mode);
  TEST_ASSERT_EQUAL_UINT16(belt::kStatusNoCommandAge, s.commandAgeMs);  // nothing applied yet
  TEST_ASSERT_EQUAL_UINT32(100, s.uptimeMs);
  TEST_ASSERT_EQUAL_UINT32(0xdeadbeefu, s.buildId);
  TEST_ASSERT_EQUAL_UINT8(190, s.maxDuty);
  TEST_ASSERT_EQUAL_UINT8(5, s.count);

  Command c = levelsCommand(0, 250);
  c.seq = 42;
  c.levels[2] = 150;
  hc.onCommand(c, 1000);
  hc.update(1030, out);
  s = belt::makeStatus(hc, 1030, 0, 255);
  TEST_ASSERT_EQUAL(StatusMode::Levels, s.mode);
  TEST_ASSERT_EQUAL_UINT8(42, s.seq);
  TEST_ASSERT_EQUAL_UINT16(30, s.commandAgeMs);
  const uint8_t expected[] = {0, 0, 150, 0, 0};
  TEST_ASSERT_EQUAL_UINT8_ARRAY(expected, s.levels, 5);

  hc.update(1250, out);  // TTL lapsed: the fault pattern's first pulse
  s = belt::makeStatus(hc, 1250, 0, 255);
  TEST_ASSERT_EQUAL(StatusMode::Expired, s.mode);
  TEST_ASSERT_EQUAL_UINT8(42, s.seq);  // still the last applied command
  TEST_ASSERT_EQUAL_UINT16(250, s.commandAgeMs);
  TEST_ASSERT_TRUE(allEqual(s.levels, 5, t.faultLevel));

  hc.update(1000 + 70000, out);  // age saturates; fault pattern has gone dormant
  s = belt::makeStatus(hc, 1000 + 70000, 0, 255);
  TEST_ASSERT_EQUAL(StatusMode::Dormant, s.mode);
  TEST_ASSERT_EQUAL_UINT16(belt::kStatusNoCommandAge, s.commandAgeMs);
}

void test_status_modes() {
  HapticController hc(5);
  uint8_t out[5];
  hc.onCommand(modeCommand(Mode::Off), 0);
  hc.update(1, out);
  TEST_ASSERT_EQUAL(StatusMode::Off, belt::statusMode(hc));
  hc.onCommand(modeCommand(Mode::Fault), 10);
  hc.update(11, out);
  TEST_ASSERT_EQUAL(StatusMode::Fault, belt::statusMode(hc));
  hc.onCommand(levelsCommand(100), 20);
  hc.onLinkLost(30);
  hc.update(31, out);
  TEST_ASSERT_EQUAL(StatusMode::Disconnected, belt::statusMode(hc));  // BLE drop, not a TTL lapse
  hc.onCommand(levelsCommand(100), 40);  // reconnected
  hc.update(41, out);
  TEST_ASSERT_EQUAL(StatusMode::Levels, belt::statusMode(hc));
  hc.update(40 + 250, out);
  TEST_ASSERT_EQUAL(StatusMode::Expired, belt::statusMode(hc));
  hc.onLinkLost(400);  // a disconnect after the TTL lapsed keeps the first cause
  TEST_ASSERT_EQUAL(StatusMode::Expired, belt::statusMode(hc));
  hc.reset();
  TEST_ASSERT_EQUAL(StatusMode::Idle, belt::statusMode(hc));
  TEST_ASSERT_FALSE(hc.hasCommand());
  TEST_ASSERT_EQUAL_STRING("expired", belt::toString(StatusMode::Expired));
}

void test_notifier_fires_on_mode_change_and_periodically() {
  belt::StatusNotifier n(250);
  TEST_ASSERT_TRUE(n.shouldNotify(StatusMode::Idle, 0));  // first status goes out at once
  TEST_ASSERT_FALSE(n.modeChanged());
  TEST_ASSERT_FALSE(n.shouldNotify(StatusMode::Idle, 5));
  TEST_ASSERT_FALSE(n.shouldNotify(StatusMode::Idle, 249));
  TEST_ASSERT_TRUE(n.shouldNotify(StatusMode::Idle, 250));  // periodic
  TEST_ASSERT_FALSE(n.modeChanged());
  TEST_ASSERT_TRUE(n.shouldNotify(StatusMode::Levels, 260));  // mode change: immediately
  TEST_ASSERT_TRUE(n.modeChanged());
  TEST_ASSERT_FALSE(n.shouldNotify(StatusMode::Levels, 265));
  TEST_ASSERT_TRUE(n.shouldNotify(StatusMode::Fault, 266));
  TEST_ASSERT_TRUE(n.modeChanged());
  TEST_ASSERT_FALSE(n.shouldNotify(StatusMode::Fault, 266 + 249));
  TEST_ASSERT_TRUE(n.shouldNotify(StatusMode::Fault, 266 + 250));  // period restarts at the change
}

void test_notifier_period_survives_millis_wraparound() {
  belt::StatusNotifier n(250);
  TEST_ASSERT_TRUE(n.shouldNotify(StatusMode::Levels, 0xFFFFFF80u));
  TEST_ASSERT_FALSE(n.shouldNotify(StatusMode::Levels, 0x00000010u));  // 144 ms later
  TEST_ASSERT_TRUE(n.shouldNotify(StatusMode::Levels, 0x0000007Au));   // 250 ms later
}

void test_notify_on_ttl_expiry_in_the_same_loop_iteration() {
  // The main loop: update the controller, then ask the notifier. The TTL lapse must
  // go out the iteration it happens, not at the next periodic tick.
  HapticController hc(5);
  belt::StatusNotifier n(250);
  uint8_t out[5];
  uint32_t notifiedExpiredAt = 0;
  hc.onCommand(levelsCommand(200, 250), 1000);
  for (uint32_t now = 1000; now <= 1400; now += 5) {
    hc.update(now, out);
    if (n.shouldNotify(belt::statusMode(hc), now) && n.modeChanged() &&
        belt::statusMode(hc) == StatusMode::Expired)
      notifiedExpiredAt = now;
  }
  TEST_ASSERT_EQUAL_UINT32(1250, notifiedExpiredAt);
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
  RUN_TEST(test_status_golden_vector);
  RUN_TEST(test_status_built_from_the_controller);
  RUN_TEST(test_status_modes);
  RUN_TEST(test_notifier_fires_on_mode_change_and_periodically);
  RUN_TEST(test_notifier_period_survives_millis_wraparound);
  RUN_TEST(test_notify_on_ttl_expiry_in_the_same_loop_iteration);
  return UNITY_END();
}
