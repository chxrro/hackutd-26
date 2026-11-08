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
      ("belt disconnected", condition { $0.status = HubStatus(belt: "disconnected") }, .beltLink),
      ("belt connecting", condition { $0.status = HubStatus(belt: "connecting") }, .beltLink),
      ("belt disconnected (unhealthy)",
       condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "belt disconnected", belt: "disconnected") },
       .beltLink),
      ("belt not acknowledging",
       condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "belt not acknowledging", belt: "connected") },
       .beltNotResponding),
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

  // MARK: Paused, faults while paused, resuming

  /// What the hub sends while paused (pause cue, reminder or quiet between them).
  private func pausedStatus(mode: String = "off", levels: [Int] = [0, 0, 0, 0, 0], belt: String = "connected") -> HubStatus {
    HubStatus(mode: mode, healthy: false, reason: "alerts paused", levels: levels, belt: belt, paused: true)
  }

  private var paused: WearerCondition { condition { $0.status = self.pausedStatus() } }

  func testPausedSaysAlertsAreOffAndTheBeltRemindsNeverThatItIsOff() {
    let c = paused
    XCTAssertEqual(c.kind, .paused)
    XCTAssertEqual(c.sentence, "Paused")
    XCTAssertEqual(c.tone, .warning)
    XCTAssertEqual(
      c.detail,
      "Alerts are off; the belt pulses softly every 15 seconds as a reminder. Silence does not mean the path is clear.")
    XCTAssertFalse(c.detail.lowercased().contains("belt is off"))
  }

  func testPauseCueAndReminderLevelsStayPaused() {
    // Cue and reminder are `mode: "levels"` with nonzero levels: still just paused, not obstacles.
    XCTAssertEqual(condition { $0.status = self.pausedStatus(mode: "levels", levels: [200, 0, 0, 0, 200]) }.kind, .paused)
    XCTAssertEqual(condition { $0.status = self.pausedStatus(mode: "levels", levels: [110, 0, 0, 0, 110]) }.kind, .paused)
    // An older hub that leaves `reason` empty while paused is still just paused.
    XCTAssertEqual(condition { $0.status = HubStatus(mode: "off", healthy: false, reason: nil, paused: true) }.kind, .paused)
  }

  func testEachFaultWhilePausedIsTheHeadline() {
    // (name, condition, de-duplication key, what the sentence names)
    let cases: [(String, WearerCondition, String, String)] = [
      ("no camera frames", condition {
        $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s", paused: true)
      }, "no camera frames", "no camera frames"),
      ("perception failing", condition {
        $0.status = HubStatus(mode: "fault", healthy: false, reason: "perception failing: model crashed", paused: true)
      }, "perception failing: model crashed", "perception failing: model crashed"),
      ("perception stalled", condition {
        $0.status = HubStatus(mode: "fault", healthy: false, reason: "perception stalled for 2.5 s", paused: true)
      }, "perception stalled", "perception stalled"),
      ("belt disconnected", condition {
        $0.status = HubStatus(mode: "fault", healthy: false, reason: "belt disconnected", belt: "disconnected", paused: true)
      }, "beltLink", "belt disconnected"),
      ("belt connecting", condition {
        $0.status = HubStatus(mode: "fault", healthy: false, reason: "belt connecting", belt: "connecting", paused: true)
      }, "beltLink", "belt connecting"),
      ("belt not acknowledging", condition {
        $0.status = HubStatus(mode: "fault", healthy: false, reason: "belt not acknowledging", belt: "connected", paused: true)
      }, "beltNotResponding", "belt not responding"),
      // Seen by the app before the hub reports it.
      ("app: no camera frames", condition { $0.status = self.pausedStatus(); $0.cameraFrameAge = 2.5 },
       "no camera frames", "no camera frames"),
      // An older hub that still says "alerts paused" while its belt is gone.
      ("belt state only", condition { $0.status = self.pausedStatus(belt: "disconnected") }, "beltLink", "belt disconnected"),
      ("fault mode only", condition { $0.status = self.pausedStatus(mode: "fault") }, "belt fault pattern", "belt fault pattern"),
    ]
    for (name, c, key, text) in cases {
      XCTAssertEqual(c.kind, .pausedFault(key), name)
      XCTAssertEqual(c.sentence, "Warning: \(text) (alerts paused)", name)
      XCTAssertEqual(c.tone, .warning, name)
      XCTAssertTrue(c.kind.isFault, name)
    }
    // Same fault key whatever the number, so it isn't re-announced every status.
    let a = condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s", paused: true) }
    let b = condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 3.0 s", paused: true) }
    XCTAssertEqual(a.kind, b.kind)
    // The app's own frame check and the hub's agree on the key.
    XCTAssertEqual(a.kind, condition { $0.status = self.pausedStatus(); $0.cameraFrameAge = 2.5 }.kind)
  }

  func testStaleNeverHidesBehindPaused() {
    XCTAssertEqual(condition { $0.status = HubStatus(healthy: false, paused: true); $0.statusAge = 5 }.kind, .statusStale)
    XCTAssertEqual(condition { $0.status = self.pausedStatus(); $0.link = .disconnected }.kind, .hubDisconnected)
  }

  private func resuming(for seconds: TimeInterval?) -> WearerCondition {
    condition {
      $0.status = HubStatus(mode: "levels", healthy: false, reason: "resuming alerts", levels: [0, 0, 200, 0, 0], paused: false)
      $0.resumingFor = seconds
    }
  }

  func testShortResumeWindowIsNotAWarning() {
    for seconds in [nil, 0, 0.6, 2.0] as [TimeInterval?] {
      let c = resuming(for: seconds)
      XCTAssertEqual(c.kind, .resuming, "\(String(describing: seconds))")
      XCTAssertEqual(c.sentence, "Resuming alerts")
      XCTAssertFalse(c.sentence.hasPrefix("Warning"))
      XCTAssertNotEqual(c.tone, .ok)  // the hub isn't vouching yet: not calm
      XCTAssertFalse(c.kind.isFault)
    }
  }

  func testStuckResumeIsAWarning() {
    let c = resuming(for: 2.1)
    XCTAssertEqual(c.kind, .resumeStuck)
    XCTAssertEqual(c.sentence, "Warning: alerts not resumed")
    XCTAssertEqual(c.tone, .warning)
    XCTAssertTrue(c.kind.isFault)
  }

  func testFaultOutranksResuming() {
    // The hub puts a fault in `reason` instead of "resuming alerts"; the app's own checks win too.
    let c = condition {
      $0.status = HubStatus(mode: "levels", healthy: false, reason: "resuming alerts", paused: false)
      $0.cameraFrameAge = 2.5
    }
    XCTAssertEqual(c.kind, .noCameraFrames)
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
            condition { $0.status = HubStatus(belt: "simulated") }, condition { $0.session = .starting },
            paused, resuming(for: 0.3), resuming(for: 5),
            condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s", paused: true) }]
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
    let paused = self.paused
    let belt = condition { $0.status = HubStatus(belt: "disconnected") }
    let fault = condition { $0.status = HubStatus(healthy: false, reason: "no camera frames for 1.2 s") }
    let starting = condition { $0.session = .starting }
    let off = WearerStatus.condition(WearerInputs(paired: true))

    XCTAssertEqual(WearerStatus.announcement(from: on, to: fault), "Warning: no camera frames.")
    XCTAssertEqual(WearerStatus.announcement(from: fault, to: on), "Recovered. Alerts on.")
    XCTAssertEqual(WearerStatus.announcement(from: on, to: paused),
                   "Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder.")
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

  func testAnnouncementsForPauseFaultsAndResume() {
    let on = condition()
    let paused = self.paused
    let pausedFault = condition {
      $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s", paused: true)
    }
    let pausedBelt = condition {
      $0.status = HubStatus(mode: "fault", healthy: false, reason: "belt disconnected", belt: "disconnected", paused: true)
    }
    let pausedBeltConnecting = condition {
      $0.status = HubStatus(mode: "fault", healthy: false, reason: "belt connecting", belt: "connecting", paused: true)
    }
    let pausedNoAck = condition {
      $0.status = HubStatus(mode: "fault", healthy: false, reason: "belt not acknowledging", belt: "connected", paused: true)
    }
    let fault = condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s") }
    let short = resuming(for: 0.4)
    let stuck = resuming(for: 2.5)
    let say = WearerStatus.announcement

    // A fault while paused is announced as a warning, not hidden under "Paused".
    XCTAssertEqual(say(paused, pausedFault), "Warning: no camera frames. Alerts paused.")
    XCTAssertEqual(say(paused, pausedBelt), "Warning: belt disconnected. Alerts paused.")
    XCTAssertEqual(say(paused, pausedNoAck), "Warning: belt not responding. Alerts paused.")
    XCTAssertNil(say(pausedBelt, pausedBeltConnecting))  // reconnect step: same problem, said once
    XCTAssertEqual(say(fault, pausedFault), "Warning: no camera frames. Alerts paused.")
    XCTAssertEqual(say(on, pausedFault), "Warning: no camera frames. Alerts paused.")
    // Recovery while still paused never sounds like "all clear".
    XCTAssertEqual(say(pausedFault, paused), "Recovered. Still paused. Alerts are off.")
    XCTAssertEqual(say(pausedBelt, paused), "Recovered. Still paused. Alerts are off.")
    // Resumed during a fault: the fault is still the news.
    XCTAssertEqual(say(pausedFault, fault), "Resumed. Warning: no camera frames.")
    XCTAssertEqual(say(pausedFault, on), "Recovered. Alerts on.")

    // The resume cue window says nothing...
    XCTAssertNil(say(paused, short))
    XCTAssertNil(say(fault, short))
    // ...and the state after it is announced once, worded against what was heard.
    XCTAssertEqual(say(paused, on), "Resumed. Alerts on.")
    XCTAssertEqual(say(short, on), "Resumed. Alerts on.")
    XCTAssertEqual(say(fault, on), "Recovered. Alerts on.")
    XCTAssertEqual(say(short, paused), "Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder.")
    // Stuck: warn, without claiming a resume.
    XCTAssertEqual(say(paused, stuck), "Warning: alerts not resumed.")
    XCTAssertEqual(say(short, stuck), "Warning: alerts not resumed.")
    XCTAssertEqual(say(stuck, on), "Recovered. Alerts on.")

    // Leaving pause for a state that can't confirm alerts are back doesn't say "Resumed".
    XCTAssertEqual(say(paused, condition { $0.link = .disconnected }), "Warning: hub not connected.")
    XCTAssertEqual(say(paused, condition { $0.statusAge = 5 }), "Warning: no status from hub.")
    XCTAssertEqual(say(paused, WearerStatus.condition(WearerInputs(paired: true))), "Alerts off.")
  }

  // MARK: Belt link and acknowledgement (hub PR #23)

  private func beltStatus(_ reason: String?, belt: String, healthy: Bool = false) -> WearerCondition {
    condition { $0.status = HubStatus(mode: healthy ? "levels" : "fault", healthy: healthy, reason: reason, belt: belt) }
  }

  func testBeltNotAcknowledgingHasItsOwnSentence() {
    let c = beltStatus("belt not acknowledging", belt: "connected")
    XCTAssertEqual(c.kind, .beltNotResponding)
    XCTAssertEqual(c.sentence, "Warning: belt not responding")
    XCTAssertEqual(c.tone, .warning)
    XCTAssertTrue(c.kind.isFault)
    XCTAssertTrue(c.detail.contains("fault pattern"))
    XCTAssertEqual(WearerStatus.announcement(from: condition(), to: c), "Warning: belt not responding.")
    XCTAssertEqual(WearerStatus.announcement(from: c, to: condition()), "Recovered. Alerts on.")
  }

  func testBeltReasonsAndBeltFieldAreOneKind() {
    // New hub (reason + healthy:false) and old hub (belt field only, healthy:true) agree.
    let newDown = beltStatus("belt disconnected", belt: "disconnected")
    let oldDown = beltStatus(nil, belt: "disconnected", healthy: true)
    let newConnecting = beltStatus("belt connecting", belt: "connecting")
    let oldConnecting = beltStatus(nil, belt: "connecting", healthy: true)
    for c in [newDown, oldDown, newConnecting, oldConnecting] { XCTAssertEqual(c.kind, .beltLink) }
    XCTAssertEqual(newDown.sentence, "Warning: belt disconnected")
    XCTAssertEqual(oldDown, newDown)
    XCTAssertEqual(newConnecting.sentence, "Warning: belt connecting")
    // The firmware plays the fault pattern on disconnect: don't promise silence.
    XCTAssertFalse(newDown.detail.contains("No vibration"))
  }

  func testCameraFaultOutranksBeltReason() {
    // Hub precedence: a camera fault is in `reason` even while the belt field says disconnected.
    let c = beltStatus("no camera frames for 1.2 s", belt: "disconnected")
    XCTAssertEqual(c.kind, .fault("no camera frames"))
    // Link down outranks not acknowledging.
    XCTAssertEqual(beltStatus("belt not acknowledging", belt: "connecting").kind, .beltLink)
  }

  func testReconnectCycleAnnouncesOnce() {
    let on = condition()
    let noAck = beltStatus("belt not acknowledging", belt: "connected")
    let down = beltStatus("belt disconnected", belt: "disconnected")
    let connecting = beltStatus("belt connecting", belt: "connecting")
    let say = WearerStatus.announcement
    XCTAssertEqual(say(on, down), "Warning: belt disconnected.")
    XCTAssertNil(say(down, connecting))
    XCTAssertNil(say(connecting, down))
    XCTAssertEqual(say(connecting, on), "Belt connected. Alerts on.")
    XCTAssertEqual(say(noAck, down), "Warning: belt disconnected.")

    // Through the announcer, with flapping between disconnected and connecting and
    // between the old and new hub wording, well apart in time so the rate limit isn't what hides it.
    let texts = spoken([
      (10, down), (14, connecting), (18, beltStatus(nil, belt: "disconnected", healthy: true)), (22, connecting),
      (26, beltStatus(nil, belt: "connecting", healthy: true)), (30, on),
    ], start: on)
    XCTAssertEqual(texts, ["Warning: belt disconnected.", "Belt connected. Alerts on."])

    // Automatic reconnect after lost writes: not responding, then the link cycle, then back.
    let cycle = spoken([(10, noAck), (14, down), (18, connecting), (22, on)], start: on)
    XCTAssertEqual(cycle, ["Warning: belt not responding.", "Warning: belt disconnected.", "Belt connected. Alerts on."])
  }

  // MARK: Announcer: the resume window end to end

  /// Feeds conditions in order (time, condition) and collects what would be spoken.
  private func spoken(_ steps: [(TimeInterval, WearerCondition)], start: WearerCondition? = nil) -> [String] {
    var announcer = WearerAnnouncer(initial: start ?? condition(), minInterval: 3)
    return steps.compactMap { announcer.update($0.1, now: $0.0)?.announcement.text }
  }

  func testAnnouncerShortResumeWindowSpeaksOnlyAlertsOn() {
    let on = condition()
    let short = resuming(for: 0.2)
    let texts = spoken([
      (10, paused), (20, short), (20.25, resuming(for: 0.45)), (20.6, on), (21, on),
    ], start: on)
    XCTAssertEqual(texts, ["Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder.",
                           "Resumed. Alerts on."])
  }

  func testAnnouncerStuckResumeWarnsOnce() {
    let on = condition()
    let texts = spoken([
      (10, paused), (20, resuming(for: 0)), (21, resuming(for: 1)), (22.1, resuming(for: 2.1)),
      (23, resuming(for: 3)), (30, on),
    ], start: on)
    XCTAssertEqual(texts, ["Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder.",
                           "Warning: alerts not resumed.", "Recovered. Alerts on."])
  }

  func testAnnouncerResumeRightAfterPauseKeepsTheRateLimit() {
    // Pause, then resume within the 3 s window: "Paused" is spoken, "Resumed. Alerts on." waits its turn.
    let on = condition()
    var announcer = WearerAnnouncer(initial: on, minInterval: 3)
    XCTAssertEqual(announcer.update(paused, now: 0)?.announcement.text,
                   "Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder.")
    XCTAssertNil(announcer.update(resuming(for: 0), now: 1))
    XCTAssertNil(announcer.update(on, now: 1.6))
    XCTAssertNil(announcer.update(on, now: 2.9))
    let held = announcer.update(on, now: 3)
    XCTAssertEqual(held?.announcement.text, "Resumed. Alerts on.")
    XCTAssertEqual(held?.tone, .ok)
    XCTAssertNil(announcer.update(on, now: 10))
  }

  func testAnnouncerFaultDuringPauseThenRecovery() {
    let on = condition()
    let pausedFault = condition {
      $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s", paused: true)
    }
    let later = condition {
      $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 4.0 s", paused: true)
    }
    let texts = spoken([(0, paused), (10, pausedFault), (11, later), (20, paused), (30, resuming(for: 0)), (30.6, on)], start: on)
    XCTAssertEqual(texts, [
      "Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder.",
      "Warning: no camera frames. Alerts paused.",
      "Recovered. Still paused. Alerts are off.",
      "Resumed. Alerts on.",
    ])
  }

  func testAnnouncerDropsAFlapBackToWhatWasHeard() {
    let on = condition()
    let fault = condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s") }
    var announcer = WearerAnnouncer(initial: on, minInterval: 3)
    XCTAssertNotNil(announcer.update(fault, now: 0))
    XCTAssertNil(announcer.update(on, now: 1))  // held
    XCTAssertNil(announcer.update(fault, now: 2))  // back to what was heard: dropped
    XCTAssertNil(announcer.update(fault, now: 10))
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

  // MARK: Stop (end_session)

  private func stopped(_ stop: WearerStop?, hubStopped: Bool? = nil, edit: (inout WearerInputs) -> Void = { _ in })
    -> WearerCondition
  {
    var input = WearerInputs(
      session: .off, paired: true, link: .connected, status: HubStatus(), statusAge: 0.2,
      wearerStop: stop, hubStopped: hubStopped)
    edit(&input)
    return WearerStatus.condition(input)
  }

  func testStopConfirmedSaysTheBeltWillNotAlert() {
    let c = stopped(.confirmed)
    XCTAssertEqual(c.kind, .stoppedByWearer)
    XCTAssertEqual(c.sentence, "Stopped")
    XCTAssertTrue(c.detail.hasPrefix("The belt will not alert."))
    XCTAssertTrue(c.detail.contains("Silence does not mean the path is clear"))
    XCTAssertEqual(c.tone, .off)
    XCTAssertEqual(WearerStatus.announcement(from: condition(), to: c), "Stopped. The belt will not alert.")
    // The hub's own status wins: it says stopped even before the reply arrives.
    XCTAssertEqual(stopped(.sending, hubStopped: true).kind, .stoppedByWearer)
    XCTAssertEqual(stopped(.notConfirmed("No reply."), hubStopped: true).kind, .stoppedByWearer)
    // Link lost after the hub confirmed: the stop lives on the hub, so it still stands.
    XCTAssertEqual(stopped(.confirmed, edit: { $0.link = .disconnected; $0.status = nil; $0.statusAge = nil }).kind,
                   .stoppedByWearer)
  }

  func testStopWaitingForTheHubIsShownButNotSpoken() {
    let c = stopped(.sending)
    XCTAssertEqual(c.kind, .stopping)
    XCTAssertNil(WearerStatus.announcement(from: condition(), to: c))
  }

  func testStopTheHubWasNotToldIsAWarningAboutTheFaultPattern() {
    for why in ["No hub control code.", "The hub isn’t connected.", "The hub refused: not paired.",
                "The hub refused: unknown action.", "No reply from the hub. It may not support settings yet."] {
      let c = stopped(.notConfirmed(why))
      XCTAssertEqual(c.kind, .stopNotConfirmed)
      XCTAssertEqual(c.tone, .warning)
      XCTAssertTrue(c.detail.hasPrefix(why))
      XCTAssertTrue(c.detail.contains("fault pattern"))
      XCTAssertEqual(WearerStatus.announcement(from: condition(), to: c),
                     "Stopped. The hub wasn’t told, so the belt plays its fault pattern.")
    }
  }

  func testStopUndoneWhenAFreshStatusSaysTheHubIsNoLongerStopped() {
    let undone = stopped(.confirmed, hubStopped: false)
    XCTAssertEqual(undone.kind, .stopUndone)
    XCTAssertEqual(undone.tone, .warning)
    XCTAssertEqual(WearerStatus.announcement(from: stopped(.confirmed), to: undone),
                   "Warning: the hub is no longer stopped. The belt may play its fault pattern.")
    XCTAssertEqual(stopped(.confirmed, hubStopped: false, edit: { $0.statusAge = 5 }).kind, .stoppedByWearer)
  }

  func testNoStopFallsBackToAlertsOff() {
    XCTAssertEqual(stopped(nil).kind, .off)
    // A session that failed by itself (backgrounded, feed lost) is not a wearer's stop.
    XCTAssertEqual(stopped(nil, edit: { $0.session = .stopped(reason: "Stopped when the app went into the background.") }).kind,
                   .stopped)
  }

  func testAnnouncerSaysStoppedOnce() {
    let on = condition()
    let texts = spoken([
      (10, stopped(.sending)), (10.2, stopped(.sending)), (10.3, stopped(.confirmed)),
      (10.5, stopped(.confirmed, hubStopped: true)), (11, stopped(.confirmed, hubStopped: true)),
      (20, stopped(.confirmed, hubStopped: true)),
    ], start: on)
    XCTAssertEqual(texts, ["Stopped. The belt will not alert."])
  }

  func testAnnouncerSaysStoppedOnceWhenTheReplyComesRightAfterAnotherAnnouncement() {
    // A fault was just spoken; "Stopped" waits for the rate limit, then is spoken once.
    let fault = condition { $0.status = HubStatus(mode: "fault", healthy: false, reason: "no camera frames for 1.2 s") }
    var announcer = WearerAnnouncer(initial: condition(), minInterval: 3)
    XCTAssertNotNil(announcer.update(fault, now: 0))
    XCTAssertNil(announcer.update(stopped(.sending), now: 1))
    XCTAssertNil(announcer.update(stopped(.confirmed), now: 1.2))
    XCTAssertEqual(announcer.update(stopped(.confirmed), now: 3)?.announcement.text, "Stopped. The belt will not alert.")
    XCTAssertNil(announcer.update(stopped(.confirmed, hubStopped: true), now: 4))
    XCTAssertNil(announcer.update(stopped(.confirmed, hubStopped: true), now: 10))
  }

  func testAnnouncerStopNotConfirmedWarnsOnce() {
    let texts = spoken([
      (10, stopped(.sending)), (13, stopped(.notConfirmed("No reply."))), (14, stopped(.notConfirmed("No reply."))),
    ], start: condition())
    XCTAssertEqual(texts, ["Stopped. The hub wasn’t told, so the belt plays its fault pattern."])
  }

  func testStopWordingNeverClaimsAClearPathOrGivesTravelAdvice() {
    let all = [stopped(.sending), stopped(.confirmed), stopped(.notConfirmed("No reply.")), stopped(.confirmed, hubStopped: false)]
    let banned = ["clear path", "safe", "go left", "go right", "move ", "turn ", "walk "]
    for c in all {
      let text = (c.sentence + " " + c.detail).lowercased()
      for word in banned { XCTAssertFalse(text.contains(word), "\(word) in \(text)") }
    }
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
