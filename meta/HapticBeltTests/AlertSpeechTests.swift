import XCTest

final class AlertSpeechTests: XCTestCase {
  private func status(hazards: Any? = nil, levels: [Int] = [0, 0, 200, 0, 0]) -> HubStatus {
    var value: [String: Any] = ["type": "status", "mode": "levels", "healthy": true,
      "levels": levels, "belt": "simulated", "frame_age_ms": 20]
    if let hazards { value["hazards"] = hazards }
    let data = try! JSONSerialization.data(withJSONObject: value)
    return HubStatus.parse(String(decoding: data, as: UTF8.self))!
  }
  private func envelope(age: Double = 30, evidence: String = "flow_crossing", motion: String = "crossing",
                        sector: Int = 2, level: Int = 200, label: Any = "person", version: Int = 1) -> [String: Any] {
    ["version": version, "frame_seq": 42, "result_age_ms": age, "events": [[
      "id": "2:flow_crossing:7", "sector": sector, "level": level, "evidence": evidence,
      "motion": motion, "label": label,
    ]]]
  }
  private func assess(_ s: HubStatus, now: Double = 10) -> AlertAssessment {
    AlertAssessment.evaluate(connected: true, streaming: true, status: s, receivedAt: 10, now: now)
  }
  func testAttributedMotionSuppliesSpeechAndSameHapticLevel() {
    let a = assess(status(hazards: envelope()))
    XCTAssertEqual(a.speech, "Person crossing, ahead.")
    var p = PhoneHapticPolicy()
    XCTAssertEqual(p.step(assessment: a, enabled: true, active: true, now: 10).cue, .obstacle(level: 200))
  }
  func testOldHubFallsBackToGenericObstacleWithoutInventingLabel() {
    let a = assess(status())
    XCTAssertEqual(a.kind, .obstacle)
    XCTAssertEqual(a.speech, "Obstacle ahead.")
  }
  func testDepthNeverBorrowsAnObjectLabel() {
    let a = assess(status(hazards: envelope(evidence: "depth", motion: "unknown", label: NSNull())))
    XCTAssertEqual(a.speech, "Obstacle ahead.")
    XCTAssertEqual(assess(status(hazards: envelope(evidence: "depth", motion: "unknown"))).kind, .fault)
  }
  func testMalformedOrMismatchedHazardsFailClosed() {
    let cases: [Any] = [NSNull(), "bad", envelope(sector: 10), envelope(level: 99),
                       envelope(version: 2), envelope(evidence: "unknown", motion: "crossing"),
                       ["version": 1, "frame_seq": 1, "result_age_ms": 20, "events": []]]
    for value in cases { XCTAssertEqual(assess(status(hazards: value)).kind, .fault) }
  }
  func testResultAgeAccruesEvenWhileCameraAndStatusLookFresh() {
    let s = status(hazards: envelope(age: 900))
    XCTAssertEqual(assess(s).kind, .obstacle)
    XCTAssertEqual(assess(s, now: 10.2).kind, .fault)
  }
  func testSpeechDoesNotRepeatEachFrameOrQueueChangedHazards() {
    var p = SpeechPolicy()
    let a = assess(status(hazards: envelope()))
    XCTAssertNotNil(p.step(a, enabled: true, active: true, detailed: true, now: 10).phrase)
    XCTAssertNil(p.step(a, enabled: true, active: true, detailed: true, now: 20).phrase)
    let b = assess(status(hazards: envelope(evidence: "box_ttc", motion: "approaching")))
    let changed = p.step(b, enabled: true, active: true, detailed: true, now: 21)
    XCTAssertTrue(changed.cancel)
    XCTAssertEqual(changed.phrase, "Person getting closer, ahead.")
    XCTAssertNil(p.step(a, enabled: true, active: true, detailed: true, now: 22).phrase)
    XCTAssertEqual(p.step(a, enabled: true, active: true, detailed: true, now: 24).phrase, a.speech)
  }
  func testFaultPreemptsSpeechAndDisablingOrBackgroundingCancels() {
    for active in [true, false] {
      var p = SpeechPolicy()
      _ = p.step(assess(status()), enabled: true, active: true, detailed: true, now: 10)
      let fault = p.step(.fault("Camera unavailable."), enabled: true, active: true, detailed: true, now: 10.1)
      XCTAssertTrue(fault.cancel)
      XCTAssertEqual(fault.phrase, "Camera unavailable.")
      let off = p.step(assess(status()), enabled: !active, active: active, detailed: true, now: 10.2)
      XCTAssertTrue(off.cancel)
      XCTAssertNil(off.phrase)
    }
  }
  func testBriefSpeechOmitsClassAndMotion() {
    var p = SpeechPolicy()
    let a = assess(status(hazards: envelope()))
    XCTAssertEqual(p.step(a, enabled: true, active: true, detailed: false, now: 10).phrase, "Obstacle ahead.")
  }

  func testDisabledWatchdogDoesNotCancelManualSpeechAfterAutomaticSpeechStops() {
    var p = SpeechPolicy()
    let a = assess(status())
    _ = p.step(a, enabled: true, active: true, detailed: true, now: 10)
    // The test button disables automatic speech before starting its own utterance.
    XCTAssertTrue(p.step(a, enabled: false, active: true, detailed: true, now: 11).cancel)
    for tick in 1...30 {
      let output = p.step(a, enabled: false, active: true, detailed: true,
                          now: 11 + Double(tick) / 10)
      XCTAssertFalse(output.cancel)
      XCTAssertNil(output.phrase)
    }
  }
}
