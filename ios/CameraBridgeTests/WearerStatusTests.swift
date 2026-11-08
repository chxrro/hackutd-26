import XCTest

final class WearerStatusTests: XCTestCase {
  /// Running, connected, fresh healthy status, real belt, glasses: the only calm state.
  private func good(_ edit: (inout WearerInputs) -> Void = { _ in }) -> WearerInputs {
    var input = WearerInputs(
      session: .running, source: .glasses, paired: true, link: .connected,
      status: HubStatus(), statusAge: 0.2, cameraFrameAge: 0.1)
    edit(&input)
    return input
  }

  private func condition(_ edit: (inout WearerInputs) -> Void = { _ in }) -> WearerCondition {
    WearerStatus.condition(good(edit))
  }

  // MARK: State -> sentence

  func testAlertsOnOnlyWhenEverythingIsVouchedFor() {
    let c = condition()
    XCTAssertEqual(c.kind, .alertsOn)
    XCTAssertEqual(c.sentence, "Alerts on")
    XCTAssertEqual(c.tone, .ok)
    XCTAssertTrue(c.detail.contains("Silence does not mean the path is clear"))
  }

  func testOffAndNotPaired() {
    let off = WearerStatus.condition(WearerInputs(session: .off, paired: true))
    XCTAssertEqual(off.sentence, "Alerts off")
    XCTAssertEqual(off.tone, .off)
    let unpaired = WearerStatus.condition(WearerInputs(session: .off, paired: false))
    XCTAssertTrue(unpaired.detail.contains("isn’t set up"))
  }

  func testStatusAppCantVouchForIsAWarning() {
    let cases: [(String, WearerCondition, WearerKind)] = [
      ("disconnected", condition { $0.link = .disconnected }, .hubDisconnected),
      ("connecting", condition { $0.link = .connecting }, .hubDisconnected),
      ("failed", condition { $0.link = .failed("Could not connect to the server.") }, .hubDisconnected),
      ("no status yet", condition { $0.status = nil; $0.statusAge = nil }, .statusStale),
      ("status over 2 s old", condition { $0.statusAge = 2.01 }, .statusStale),
      ("unhealthy", condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s") },
       .fault("no camera frames")),
      ("paused", condition { $0.status = HubStatus(mode: "off", healthy: false, reason: "alerts paused", paused: true) },
       .paused),
      ("belt disconnected", condition { $0.status = HubStatus(belt: "disconnected") }, .belt("disconnected")),
      ("belt connecting", condition { $0.status = HubStatus(belt: "connecting") }, .belt("connecting")),
      ("belt simulated", condition { $0.status = HubStatus(belt: "simulated") }, .simulated("belt")),
      ("mock glasses", condition { $0.mockGlasses = true }, .simulated("mock")),
      ("mock glasses + simulated belt", condition { $0.mockGlasses = true; $0.status = HubStatus(belt: "simulated") },
       .simulated("mock+belt")),
      ("belt test", condition { $0.status = HubStatus(mode: "levels", healthy: false, reason: "belt test", paused: false) },
       .beltTest),
      ("belt unknown", condition { $0.status = HubStatus(belt: "melted") }, .belt("melted")),
      ("fault mode while healthy", condition { $0.status = HubStatus(mode: "fault") }, .beltMode("fault")),
      ("off mode while healthy", condition { $0.status = HubStatus(mode: "off") }, .beltMode("off")),
      ("no camera frames", condition { $0.cameraFrameAge = 2.5 }, .noCameraFrames),
      ("starting", condition { $0.session = .starting; $0.cameraFrameAge = nil }, .starting),
      ("test pattern", condition { $0.source = .test }, .simulated("test")),
      ("stopped by itself", condition { $0.session = .stopped(reason: "Glasses disconnected or ended the session.") },
       .stopped),
      ("diagnostic", condition { $0.session = .diagnostic }, .diagnostic),
    ]
    for (name, c, kind) in cases {
      XCTAssertEqual(c.kind, kind, name)
      XCTAssertEqual(c.tone, .warning, name)
      XCTAssertNotEqual(c.sentence, "Alerts on", name)
    }
  }

  func testWarningSentences() {
    XCTAssertEqual(condition { $0.status = HubStatus(healthy: false, reason: "no camera frames for 1.2 s") }.sentence,
                   "Warning: no camera frames")
    XCTAssertEqual(condition { $0.status = HubStatus(healthy: false, reason: nil) }.sentence,
                   "Warning: hub can’t vouch for alerts")
    XCTAssertEqual(condition { $0.status = HubStatus(mode: "off", healthy: false, reason: "alerts paused", paused: true) }.sentence,
                   "Paused")
    // Paused is read from the field only; a bare reason is still a warning, just not "Paused".
    XCTAssertEqual(condition { $0.status = HubStatus(mode: "off", healthy: false, reason: "alerts paused") }.sentence,
                   "Warning: alerts paused")
    XCTAssertEqual(condition { $0.mockGlasses = true; $0.status = HubStatus(belt: "simulated") }.sentence,
                   "Warning: simulation only")
    XCTAssertEqual(condition { $0.status = HubStatus(healthy: false, reason: "belt test") }.sentence, "Belt test")
    XCTAssertEqual(condition { $0.link = .disconnected }.sentence, "Warning: hub not connected")
    XCTAssertEqual(condition { $0.statusAge = 3 }.sentence, "Warning: no status from hub")
  }

  func testFaultWhilePausedStaysPausedButIsMentioned() {
    let c = condition { $0.status = HubStatus(mode: "off", healthy: false, reason: "no camera frames for 1.2 s", paused: true) }
    XCTAssertEqual(c.kind, .paused)
    XCTAssertEqual(c.detail, "No belt alerts until you resume. The hub also reports: no camera frames.")
  }

  func testPausedOutranksFaultAndStaleNeverHidesBehindPaused() {
    // Paused shows as paused even though healthy is false...
    XCTAssertEqual(condition { $0.status = HubStatus(mode: "off", healthy: false, reason: "alerts paused", paused: true) }.kind, .paused)
    // ...but a stale "paused" status is still a stale status.
    XCTAssertEqual(condition { $0.status = HubStatus(healthy: false, paused: true); $0.statusAge = 5 }.kind, .statusStale)
  }

  func testFaultKeyIgnoresChangingNumbers() {
    let a = condition { $0.status = HubStatus(healthy: false, reason: "no camera frames for 1.2 s") }
    let b = condition { $0.status = HubStatus(healthy: false, reason: "no camera frames for 1.4 s") }
    XCTAssertEqual(a.kind, b.kind)
    XCTAssertEqual(WearerStatus.shortReason("cloud classifier: no reply for 3 s"), "cloud classifier: no reply")
    XCTAssertEqual(WearerStatus.shortReason("inference slow (350 ms)"), "inference slow")
    XCTAssertEqual(WearerStatus.shortReason("  "), "hub can’t vouch for alerts")
  }

  func testNoWordingClaimsAClearPathOrGivesTravelAdvice() {
    var all: [WearerCondition] = [condition(), WearerStatus.condition(WearerInputs())]
    all += [condition { $0.link = .disconnected }, condition { $0.statusAge = 9 }, condition { $0.source = .test },
            condition { $0.mockGlasses = true }, condition { $0.status = HubStatus(healthy: false, reason: "belt test") },
            condition { $0.status = HubStatus(belt: "simulated") }, condition { $0.session = .starting }]
    let banned = ["path is clear.", "clear path", "safe", "go left", "go right", "move ", "turn ", "walk "]
    for c in all {
      let text = (c.sentence + " " + c.detail).lowercased()
      for word in banned where !(word == "path is clear." && text.contains("does not mean the path is clear")) {
        XCTAssertFalse(text.contains(word), "\(word) in \(text)")
      }
    }
  }

  // MARK: Announcements

  func testAnnouncementsForImportantChanges() {
    let on = condition()
    let paused = condition { $0.status = HubStatus(mode: "off", healthy: false, reason: "alerts paused", paused: true) }
    let belt = condition { $0.status = HubStatus(belt: "disconnected") }
    let fault = condition { $0.status = HubStatus(healthy: false, reason: "no camera frames for 1.2 s") }
    let starting = condition { $0.session = .starting }
    let off = WearerStatus.condition(WearerInputs(paired: true))

    XCTAssertEqual(WearerStatus.announcement(from: on, to: fault), "Warning: no camera frames.")
    XCTAssertEqual(WearerStatus.announcement(from: fault, to: on), "Recovered. Alerts on.")
    XCTAssertEqual(WearerStatus.announcement(from: on, to: paused), "Paused. No belt alerts.")
    XCTAssertEqual(WearerStatus.announcement(from: paused, to: on), "Resumed. Alerts on.")
    XCTAssertEqual(WearerStatus.announcement(from: on, to: belt), "Warning: belt disconnected.")
    XCTAssertEqual(WearerStatus.announcement(from: belt, to: on), "Belt connected. Alerts on.")
    XCTAssertEqual(WearerStatus.announcement(from: off, to: starting), "Starting. No alerts yet.")
    XCTAssertEqual(WearerStatus.announcement(from: starting, to: on), "Alerts on.")
    XCTAssertEqual(WearerStatus.announcement(from: on, to: off), "Alerts off.")

    // Recovery is announced even when a known simulation warning remains.
    let simBelt = condition { $0.status = HubStatus(belt: "simulated") }
    let mockAndBelt = condition { $0.mockGlasses = true; $0.status = HubStatus(belt: "simulated") }
    XCTAssertEqual(WearerStatus.announcement(from: fault, to: simBelt), "Recovered. Warning: simulated belt.")
    XCTAssertEqual(WearerStatus.announcement(from: fault, to: mockAndBelt),
                   "Recovered. Warning: simulation only: mock glasses and simulated belt.")
    XCTAssertEqual(WearerStatus.announcement(from: mockAndBelt, to: fault), "Warning: no camera frames.")
    XCTAssertEqual(WearerStatus.announcement(from: starting, to: mockAndBelt),
                   "Warning: simulation only: mock glasses and simulated belt.")
    XCTAssertEqual(WearerStatus.announcement(from: paused, to: mockAndBelt),
                   "Resumed. Warning: simulation only: mock glasses and simulated belt.")
    XCTAssertEqual(
      WearerStatus.announcement(from: on, to: condition { $0.status = HubStatus(healthy: false, reason: "belt test") }),
      "Belt test. These vibrations are not obstacles.")
    XCTAssertEqual(
      WearerStatus.announcement(from: on, to: condition { $0.session = .stopped(reason: "Glasses feed stale.") }),
      "Warning: alerts stopped. Glasses feed stale.")
  }

  func testNoAnnouncementWithoutAKindChange() {
    let a = condition { $0.status = HubStatus(healthy: false, reason: "no camera frames for 1.2 s") }
    let b = condition { $0.status = HubStatus(healthy: false, reason: "no camera frames for 1.8 s") }
    XCTAssertNil(WearerStatus.announcement(from: a, to: b))
    XCTAssertNil(WearerStatus.announcement(from: condition(), to: condition { $0.statusAge = 1.9 }))
  }

  // MARK: Gate: de-duplication and rate limiting

  private let fault = Announcement(key: "fault", text: "Warning: no camera frames.")
  private let on = Announcement(key: "alertsOn", text: "Recovered. Alerts on.")
  private let belt = Announcement(key: "belt", text: "Warning: belt disconnected.")

  func testGateSpeaksFirstAndDropsDuplicates() {
    var gate = AnnouncementGate(minInterval: 3)
    XCTAssertEqual(gate.submit(fault, now: 0), fault)
    XCTAssertNil(gate.submit(fault, now: 10))
    XCTAssertNil(gate.submit(fault, now: 20))
  }

  func testInitialKeyIsNotAnnounced() {
    var gate = AnnouncementGate(minInterval: 3, initialKey: "off")
    XCTAssertNil(gate.submit(Announcement(key: "off", text: "Alerts off."), now: 0))
  }

  func testGateHoldsNewestAndSpeaksItWhenTheWindowOpens() {
    var gate = AnnouncementGate(minInterval: 3)
    XCTAssertEqual(gate.submit(fault, now: 0), fault)
    XCTAssertNil(gate.submit(on, now: 1))
    XCTAssertNil(gate.submit(belt, now: 1.5))  // newest wins
    XCTAssertNil(gate.poll(now: 2.9))
    XCTAssertEqual(gate.poll(now: 3.0), belt)
    XCTAssertNil(gate.poll(now: 10))
  }

  func testGateDropsAFlapBackToWhatWasLastSaid() {
    var gate = AnnouncementGate(minInterval: 3)
    XCTAssertEqual(gate.submit(on, now: 0), on)
    XCTAssertNil(gate.submit(fault, now: 0.5))  // held
    XCTAssertNil(gate.submit(on, now: 1))  // back to "on": nothing to say
    XCTAssertNil(gate.poll(now: 5))
  }

  func testDropPendingSilencesAHeldAnnouncement() {
    var gate = AnnouncementGate(minInterval: 3)
    XCTAssertEqual(gate.submit(on, now: 0), on)
    XCTAssertNil(gate.submit(fault, now: 1))
    gate.dropPending()
    XCTAssertNil(gate.poll(now: 5))
  }

  func testRapidFlappingSpeaksAtMostOncePerInterval() {
    var gate = AnnouncementGate(minInterval: 3)
    var spoken: [TimeInterval] = []
    var t: TimeInterval = 0
    while t < 30 {
      let next = Int(t * 10) % 2 == 0 ? fault : on
      if gate.submit(next, now: t) != nil { spoken.append(t) }
      if gate.poll(now: t) != nil { spoken.append(t) }
      t += 0.1
    }
    for (a, b) in zip(spoken, spoken.dropFirst()) { XCTAssertGreaterThanOrEqual(b - a, 3 - 1e-9) }
    XCTAssertLessThanOrEqual(spoken.count, 11)
  }

  // MARK: Obstacle-kind speech

  func testDirections() {
    XCTAssertEqual((0..<5).compactMap { SectorSpeech.direction(index: $0, count: 5) },
                   ["far left", "left", "ahead", "right", "far right"])
    XCTAssertEqual((0..<3).compactMap { SectorSpeech.direction(index: $0, count: 3) }, ["left", "ahead", "right"])
    XCTAssertNil(SectorSpeech.direction(index: 5, count: 5))
  }

  func testPhraseFollowsTheStrongestActiveMotor() {
    let sectors = [HubStatus.Sector(kind: nil), .init(kind: "object"), .init(kind: "person"), .init(kind: nil), .init(kind: "person")]
    let status = HubStatus(levels: [0, 200, 120, 0, 0], sectors: sectors)
    XCTAssertEqual(SectorSpeech.phrase(for: status), Announcement(key: "object:left", text: "Object left"))
    XCTAssertEqual(SectorSpeech.phrase(for: HubStatus(levels: [0, 0, 120, 0, 0], sectors: sectors))?.text, "Person ahead")
    // A label without a vibrating motor isn't spoken.
    XCTAssertNil(SectorSpeech.phrase(for: HubStatus(levels: [0, 0, 0, 0, 0], sectors: sectors)))
    // Unknown kinds and mismatched lengths aren't spoken.
    XCTAssertNil(SectorSpeech.phrase(for: HubStatus(levels: [0, 0, 0, 0, 90], sectors: Array(repeating: .init(kind: "vehicle"), count: 5))))
    XCTAssertNil(SectorSpeech.phrase(for: HubStatus(levels: [0, 90], sectors: sectors)))
  }

  func testSectorSpeechIsRateLimited() {
    var gate = SectorSpeechGate(minInterval: 4, repeatInterval: 15, quietAfterState: 2)
    let person = Announcement(key: "person:ahead", text: "Person ahead")
    let object = Announcement(key: "object:left", text: "Object left")
    XCTAssertEqual(gate.submit(person, now: 0), person)
    XCTAssertNil(gate.submit(object, now: 3))  // under 4 s
    XCTAssertEqual(gate.submit(object, now: 4), object)
    XCTAssertNil(gate.submit(person, now: 10))  // same phrase within 15 s
    XCTAssertEqual(gate.submit(person, now: 15), person)
    gate.noteStateAnnouncement(now: 30)
    XCTAssertNil(gate.submit(object, now: 31))  // right after a state announcement
    XCTAssertEqual(gate.submit(object, now: 32), object)
  }
}
