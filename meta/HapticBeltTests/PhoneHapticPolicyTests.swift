import XCTest

final class PhoneHapticPolicyTests: XCTestCase {
  private func status(
    levels: [Int] = [0, 0, 200, 0, 0], healthy: Bool = true,
    mode: String = "levels", age: Double = 30
  ) -> HubStatus {
    let data = try! JSONSerialization.data(withJSONObject: [
      "type": "status", "levels": levels, "healthy": healthy, "mode": mode,
      "belt": "simulated", "frame_age_ms": age, "reason": "test fault",
    ])
    return HubStatus.parse(String(decoding: data, as: UTF8.self))!
  }

  private func sample(
    _ policy: inout PhoneHapticPolicy, now: Double = 10, receivedAt: Double? = 10,
    status: HubStatus? = nil, enabled: Bool = true, active: Bool = true,
    connected: Bool = true, streaming: Bool = true
  ) -> PhoneHapticPolicy.Output {
    policy.step(enabled: enabled, active: active, connected: connected, streaming: streaming,
                status: status ?? self.status(), receivedAt: receivedAt, now: now)
  }

  func testObstacleIntensityAndDirectionsComeFromAllFiveSectors() {
    var p = PhoneHapticPolicy()
    let result = sample(&p, status: status(levels: [100, 0, 200, 0, 80]))
    XCTAssertEqual(result.cue, .obstacle(level: 200))
    XCTAssertEqual(result.label, "Obstacle alert: left, center, right")
  }

  func testRapidStatusUpdatesCannotQueueOrAcceleratePulses() {
    var p = PhoneHapticPolicy()
    XCTAssertNotNil(sample(&p).cue)
    for i in 1...11 {
      let now = 10 + Double(i) / 10
      XCTAssertNil(sample(&p, now: now, receivedAt: now).cue)
    }
    XCTAssertNotNil(sample(&p, now: 11.21, receivedAt: 11.2).cue)
  }

  func testStaleStatusReplacesObstacleWithFaultWithoutNewMessages() {
    var p = PhoneHapticPolicy()
    _ = sample(&p)
    let stale = sample(&p, now: 11.01)
    XCTAssertEqual(stale.cue, .fault)
    XCTAssertTrue(stale.cancel)
    XCTAssertNil(sample(&p, now: 12).cue)
    XCTAssertEqual(sample(&p, now: 14.02).cue, .fault)
  }

  func testDisabledAndBackgroundedImmediatelyCancel() {
    for background in [false, true] {
      var p = PhoneHapticPolicy()
      _ = sample(&p)
      let stopped = sample(&p, enabled: background, active: !background)
      XCTAssertNil(stopped.cue)
      XCTAssertTrue(stopped.cancel)
    }
  }

  func testDisconnectAndStoppedCameraFaultEvenWithHealthyCachedStatus() {
    var p = PhoneHapticPolicy()
    XCTAssertEqual(sample(&p, connected: false).cue, .fault)
    p = PhoneHapticPolicy()
    XCTAssertEqual(sample(&p, streaming: false).cue, .fault)
  }

  func testFreshStatusCannotHideStaleFramesOrHubFault() {
    for state in [status(age: 1001), status(healthy: false), status(mode: "fault")] {
      var p = PhoneHapticPolicy()
      XCTAssertEqual(sample(&p, status: state).cue, .fault)
    }
  }

  func testMalformedLevelsAndUnknownModesNeverTriggerObstaclePulse() {
    for state in [status(levels: [1]), status(levels: [-1, 0, 0, 0, 0]),
                  status(levels: [256, 0, 0, 0, 0]), status(mode: "unknown")] {
      var p = PhoneHapticPolicy()
      XCTAssertEqual(sample(&p, status: state).cue, .fault)
    }
  }

  func testZeroLevelsCancelAndDoNotClaimClearPath() {
    var p = PhoneHapticPolicy()
    _ = sample(&p)
    let quiet = sample(&p, status: status(levels: [0, 0, 0, 0, 0]))
    XCTAssertNil(quiet.cue)
    XCTAssertTrue(quiet.cancel)
    XCTAssertTrue(quiet.label.contains("not proof"))
  }

  func testNoStatusOrTimestampFaultsAndFreshDataRecovers() {
    var p = PhoneHapticPolicy()
    XCTAssertEqual(p.step(enabled: true, active: true, connected: true, streaming: true,
                          status: nil, receivedAt: nil, now: 10).cue, .fault)
    XCTAssertEqual(sample(&p, now: 10.1).cue, .obstacle(level: 200))
    p = PhoneHapticPolicy()
    XCTAssertEqual(sample(&p, receivedAt: nil).cue, .fault)
  }
}
