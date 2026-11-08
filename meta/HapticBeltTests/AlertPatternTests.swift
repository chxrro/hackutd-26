import XCTest

final class AlertPatternTests: XCTestCase {
  private func obstacle(_ level: Int = 170, direction: String = "ahead") -> AlertAssessment {
    AlertAssessment(kind: .obstacle, level: level, label: "Obstacle alert",
                    speech: "Chair \(direction).", briefSpeech: "Obstacle \(direction).", key: direction)
  }

  func testPatternsAreDistinctFiniteAndIntensityBounded() {
    let cues: [PhoneHapticPolicy.Cue] = [.obstacle(level: 170), .urgent(level: 240), .fault]
    for strength in [Double.nan, -1, 0.2, 0.7, 1, 9] {
      for (i, cue) in cues.enumerated() {
        let pulses = cue.pulses(strength: strength)
        XCTAssertEqual(pulses.count, i + 1)
        for pulse in pulses {
          XCTAssertTrue(pulse.intensity.isFinite)
          XCTAssertGreaterThan(pulse.intensity, 0)
          XCTAssertLessThanOrEqual(pulse.intensity, 1)
          XCTAssertLessThanOrEqual(pulse.start + pulse.duration, 0.4)
        }
      }
    }
    XCTAssertLessThan(cues[0].pulses(strength: 0.2)[0].intensity,
                      cues[0].pulses(strength: 1)[0].intensity)
  }

  func testUrgentPulsePreemptsNoticeAndUsesFasterCadence() {
    var p = PhoneHapticPolicy()
    func step(_ level: Int, _ now: Double) -> PhoneHapticPolicy.Output {
      p.step(assessment: obstacle(level), enabled: true, active: true, now: now)
    }
    XCTAssertEqual(step(170, 10).cue, .obstacle(level: 170))
    let urgent = step(240, 10.1)
    XCTAssertEqual(urgent.cue, .urgent(level: 240))
    XCTAssertTrue(urgent.cancel)
    XCTAssertNil(step(240, 10.5).cue)
    XCTAssertEqual(step(240, 10.71).cue, .urgent(level: 240))
  }

  func testUrgencyHysteresisPreventsThresholdChatter() {
    XCTAssertEqual(AlertUrgency.resolve(level: 220, previous: .notice), .urgent)
    XCTAssertEqual(AlertUrgency.resolve(level: 210, previous: .urgent), .urgent)
    XCTAssertEqual(AlertUrgency.resolve(level: 199, previous: .urgent), .notice)
    XCTAssertEqual(AlertUrgency.resolve(level: 210, previous: .notice), .notice)
    var p = PhoneHapticPolicy()
    _ = p.step(assessment: obstacle(240), enabled: true, active: true, now: 0)
    XCTAssertNil(p.step(assessment: obstacle(210), enabled: true, active: true, now: 0.1).cue)
    XCTAssertEqual(p.step(assessment: obstacle(190), enabled: true, active: true, now: 0.2).cue,
                   .obstacle(level: 190))
  }

  func testVoiceEscalationPreemptsOrdinaryCooldown() {
    var p = SpeechPolicy()
    _ = p.step(obstacle(), enabled: true, active: true, detailed: true, now: 10)
    let urgent = p.step(obstacle(240), enabled: true, active: true, detailed: true, now: 10.1)
    XCTAssertEqual(urgent.phrase, "Urgent. Chair ahead.")
    XCTAssertTrue(urgent.cancel)
    XCTAssertNil(p.step(obstacle(210), enabled: true, active: true, detailed: true, now: 10.2).phrase)
    _ = p.step(obstacle(190), enabled: true, active: true, detailed: true, now: 10.3)
    XCTAssertNil(p.step(obstacle(240), enabled: true, active: true, detailed: true, now: 10.4).phrase)
  }

  func testOptionalVoiceRemindersUseOnlyLatestAssessment() {
    var p = SpeechPolicy()
    let a = obstacle()
    _ = p.step(a, enabled: true, active: true, detailed: true, now: 10, reminders: true)
    XCTAssertNil(p.step(a, enabled: true, active: true, detailed: true, now: 21.9, reminders: true).phrase)
    XCTAssertEqual(p.step(a, enabled: true, active: true, detailed: true, now: 22, reminders: true).phrase, a.speech)
    let fault = p.step(.fault("Camera unavailable."), enabled: true, active: true, detailed: true, now: 22.1, reminders: true)
    XCTAssertEqual(fault.phrase, "Camera unavailable.")
    XCTAssertNil(p.step(.fault("Camera unavailable."), enabled: true, active: true, detailed: true, now: 30, reminders: true).phrase)
    XCTAssertEqual(p.step(.fault("Camera unavailable."), enabled: true, active: true, detailed: true, now: 37.2, reminders: true).phrase, "Camera unavailable.")
  }

  func testUrgentVoiceReminderEverySixSecondsAndBriefModeRetainsUrgency() {
    var p = SpeechPolicy()
    let a = obstacle(240)
    XCTAssertEqual(p.step(a, enabled: true, active: true, detailed: false, now: 10, reminders: true).phrase,
                   "Urgent. Obstacle ahead.")
    XCTAssertNil(p.step(a, enabled: true, active: true, detailed: false, now: 15.9, reminders: true).phrase)
    XCTAssertNotNil(p.step(a, enabled: true, active: true, detailed: false, now: 16, reminders: true).phrase)
  }

  func testManualRepeatUsesFreshFaultAndIsNotQueuedAcrossBackgrounding() {
    var p = SpeechPolicy()
    _ = p.step(obstacle(), enabled: true, active: true, detailed: true, now: 10)
    p.requestRepeat()
    XCTAssertEqual(p.step(.fault("Hub disconnected."), enabled: true, active: true, detailed: true, now: 10.1).phrase,
                   "Hub disconnected.")
    p.requestRepeat()
    XCTAssertNil(p.step(obstacle(), enabled: true, active: false, detailed: true, now: 11).phrase)
  }

  func testQuietOnlySpeaksOnRequestAndDoesNotClaimClearPath() {
    let quiet = AlertAssessment(kind: .quiet, level: 0, label: "No alert", speech: "", briefSpeech: "", key: "quiet")
    var p = SpeechPolicy()
    XCTAssertNil(p.step(quiet, enabled: true, active: true, detailed: true, now: 0, reminders: true).phrase)
    p.requestRepeat()
    XCTAssertEqual(p.step(quiet, enabled: true, active: true, detailed: true, now: 1).phrase,
                   "No current alert. This does not mean the path is clear.")
    XCTAssertNil(p.step(quiet, enabled: true, active: true, detailed: true, now: 2).phrase)
  }

  func testPracticePhrasesIdentifyThemselvesAsTests() {
    for sample in AlertPatternSample.allCases {
      XCTAssertTrue(sample.explanation.hasPrefix("Pattern test."))
    }
  }
}
