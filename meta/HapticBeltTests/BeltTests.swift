import XCTest

final class BeltTests: XCTestCase {
  private func status(levels: [Int] = [0, 64, 128, 192, 255], belt: String = "simulated",
                      healthy: Bool = true, age: Double = 20, hazards: Any? = nil) -> HubStatus {
    var value: [String: Any] = ["type": "status", "mode": "levels", "healthy": healthy,
      "levels": levels, "belt": belt, "frame_age_ms": age]
    if let hazards { value["hazards"] = hazards }
    return HubStatus.parse(String(decoding: try! JSONSerialization.data(withJSONObject: value), as: UTF8.self))!
  }
  private func relay(_ s: HubStatus? = nil, now: Double = 10, enabled: Bool = true,
                     active: Bool = true, connected: Bool = true, streaming: Bool = true) -> BeltCommand {
    BeltRelayPolicy.evaluate(enabled: enabled, active: active, connected: connected,
      streaming: streaming, status: s ?? status(), receivedAt: 10, now: now).command
  }
  private func firmware(seq: UInt8 = 0, mode: UInt8 = 2, age: UInt16 = 20,
                        levels: [UInt8] = [0, 64, 128, 192, 255]) -> BeltFirmwareStatus {
    BeltFirmwareStatus(mode: mode, seq: seq, commandAgeMs: age, uptimeMs: 100,
                       buildID: 123, maxDuty: 255, levels: levels)
  }

  func testCommandMatchesSharedGoldenVector() throws {
    let c = try XCTUnwrap(BeltCommand.levels([0, 64, 128, 192, 255]))
    XCTAssertEqual([UInt8](c.encode(seq: 7)), [1, 1, 7, 0xfa, 0, 5, 0, 64, 128, 192, 255])
    XCTAssertEqual([UInt8](BeltCommand.off.encode(seq: 1)), [1, 0, 1, 0xfa, 0, 5, 0, 0, 0, 0, 0])
    XCTAssertEqual([UInt8](BeltCommand.fault.encode(seq: 2))[1], 2)
    for levels in [[1], [-1, 0, 0, 0, 0], [256, 0, 0, 0, 0]] { XCTAssertNil(BeltCommand.levels(levels)) }
  }

  func testInfoRejectsWrongProtocolMotorCountAndMalformedBuild() {
    XCTAssertEqual(BeltInfo.decode(Data([1, 5]))?.build, "unknown")
    XCTAssertEqual(BeltInfo.decode(Data([1, 5] + Array("3268a632".utf8)))?.build, "3268a632")
    for b: [UInt8] in [[], [1], [2, 5], [1, 8], [1, 5, 0xff], [1, 5] + Array(repeating: 65, count: 17)] {
      XCTAssertNil(BeltInfo.decode(Data(b)))
    }
  }

  func testStatusGoldenVectorAndMalformedPackets() throws {
    let bytes: [UInt8] = [1, 2, 7, 0x28, 0, 0x40, 0xe2, 1, 0, 0x4d, 0x3c, 0x2b, 0x1a, 255, 5, 0, 64, 128, 192, 255]
    let st = try XCTUnwrap(BeltFirmwareStatus.decode(Data(bytes)))
    XCTAssertEqual(st.seq, 7)
    XCTAssertEqual(st.commandAgeMs, 40)
    XCTAssertEqual(st.uptimeMs, 123456)
    XCTAssertEqual(st.buildID, 0x1a2b3c4d)
    XCTAssertEqual(st.levels, [0, 64, 128, 192, 255])
    XCTAssertEqual(st.modeName, "levels")
    XCTAssertNil(BeltFirmwareStatus.decode(Data(bytes.dropLast())))
    for (index, value): (Int, UInt8) in [(0, 2), (1, 7), (14, 8)] {
      var bad = bytes; bad[index] = value
      XCTAssertNil(BeltFirmwareStatus.decode(Data(bad)))
    }
  }

  func testRelayPreservesEverySectorAndRequiresSimulatedHubTransport() {
    XCTAssertEqual(relay().levels, [0, 64, 128, 192, 255])
    for name in ["connected", "connecting", "disconnected", "unknown"] {
      XCTAssertEqual(relay(status(belt: name)), .fault)
    }
  }

  func testDisabledAndInactiveAlwaysRequestOff() {
    XCTAssertEqual(relay(enabled: false), .off)
    XCTAssertEqual(relay(active: false), .off)
    XCTAssertEqual(relay(status(healthy: false), enabled: false), .off)
  }

  func testStaleDisconnectedAndMalformedInputFaults() {
    XCTAssertEqual(relay(now: 11.01), .fault)
    XCTAssertEqual(relay(now: 9), .fault)
    XCTAssertEqual(relay(connected: false), .fault)
    XCTAssertEqual(relay(streaming: false), .fault)
    for s in [status(healthy: false), status(age: 1001), status(levels: [1]), status(hazards: "bad")] {
      XCTAssertEqual(relay(s), .fault)
    }
    XCTAssertEqual(relay(status(levels: [0, 0, 0, 0, 0])), .off)
  }

  func testResultAgeAccruesLocallyBeforeSendingMotors() {
    let h: [String: Any] = ["version": 1, "frame_seq": 1, "result_age_ms": 900,
      "events": [["id": "2:depth:-", "sector": 2, "level": 128, "evidence": "depth", "motion": "unknown"]]]
    let s = status(levels: [0, 0, 128, 0, 0], hazards: h)
    XCTAssertEqual(relay(s, now: 10).mode, .levels)
    XCTAssertEqual(relay(s, now: 10.2), .fault)
  }

  func testNoWriteBacklogAndNewestCommandWinsAfterAck() throws {
    var gate = BeltWriteGate()
    let first = try XCTUnwrap(gate.take(.off, now: 0))
    XCTAssertEqual(first.seq, 0)
    XCTAssertNil(gate.take(.fault, now: 0.1))
    gate.ack(now: 0.1)
    let newest = try XCTUnwrap(gate.take(BeltCommand.levels([0, 0, 100, 0, 0])!, now: 0.11))
    XCTAssertEqual(newest.seq, 1)
    XCTAssertEqual(newest.command.levels, [0, 0, 100, 0, 0])
    XCTAssertEqual(gate.acknowledged, 1)
  }

  func testLateDeliveryDoesNotPretendAnAcknowledgedWriteIsStillPending() {
    var gate = BeltWriteGate()
    _ = gate.take(.off, now: 10)
    gate.ack(now: 10.01)
    XCTAssertNil(gate.take(.off, now: 10.05))
    XCTAssertNotNil(gate.take(.off, now: 10.1))
    XCTAssertFalse(gate.timedOut(now: 10.2))
    XCTAssertFalse(gate.timedOut(now: 10.36))
    XCTAssertTrue(gate.deliveryOverdue(now: 10.36))
    gate.ack(now: 10.4)
    XCTAssertFalse(gate.timedOut(now: 10.5)) // no outstanding ATT request
    XCTAssertTrue(gate.deliveryOverdue(now: 10.5)) // still must disarm live motor output
    XCTAssertNotNil(gate.take(.off, now: 10.5)) // recovery off can now be written
  }

  func testSequenceWrapAndHistoryBounded() {
    var gate = BeltWriteGate()
    for i in 0...256 {
      let w = gate.take(.off, now: Double(i) * 0.11)
      XCTAssertEqual(w?.seq, UInt8(truncatingIfNeeded: i))
      gate.ack(now: Double(i) * 0.11 + 0.01)
    }
    XCTAssertLessThanOrEqual(gate.history.count, 6)
  }

  func testHeartbeatLeavesRetryMarginWithoutExceedingFifteenWritesPerSecond() {
    var gate = BeltWriteGate()
    XCTAssertNotNil(gate.take(.off, now: 0))
    gate.ack(now: 0.03)
    XCTAssertNil(gate.take(.off, now: 0.06))
    XCTAssertNotNil(gate.take(.off, now: 0.075))
    gate.ack(now: 0.08)
    XCTAssertNil(gate.take(.off, now: 0.13))
    XCTAssertNotNil(gate.take(.off, now: 0.15))
    // More frequent writes must not extend the firmware/delivery deadline.
    XCTAssertTrue(gate.deliveryOverdue(now: 0.401))
  }

  func testFirmwareConfirmationRequiresRecentAppliedSequenceModeAndLevels() {
    var gate = BeltWriteGate()
    _ = gate.take(BeltCommand.levels([0, 64, 128, 192, 255])!, now: 10)
    XCTAssertTrue(gate.matches(firmware(), now: 10.1))
    XCTAssertFalse(gate.matches(firmware(seq: 8), now: 10.1))
    XCTAssertFalse(gate.matches(firmware(mode: 4), now: 10.1))
    XCTAssertFalse(gate.matches(firmware(age: 250), now: 10.1))
    XCTAssertFalse(gate.matches(firmware(levels: [0, 0, 0, 0, 0]), now: 10.1))
    XCTAssertFalse(gate.matches(firmware(), now: 10.6))
  }

  func testStatusGracePeriodThenFaultAndNoStaleConfirmation() {
    var monitor = BeltStatusMonitor(startedAt: 10)
    var gate = BeltWriteGate()
    _ = gate.take(BeltCommand.levels([0, 64, 128, 192, 255])!, now: 10)
    XCTAssertNil(monitor.fault(now: 10.9))
    XCTAssertNotNil(monitor.fault(now: 11.1))
    monitor.receive(firmware(), gate: gate, now: 10.1)
    XCTAssertTrue(monitor.confirmed)
    XCTAssertNil(monitor.fault(now: 10.1))
    monitor.receive(firmware(mode: 4), gate: gate, now: 10.2)
    XCTAssertNotNil(monitor.fault(now: 10.2))
    XCTAssertNotNil(monitor.fault(now: 11.3))
  }

  func testProducerLeaseExpiresToFaultButBenchPulseExpiresOff() {
    let cmd = BeltCommand.levels([0, 0, 120, 0, 0])!
    let live = BeltIntent(command: cmd, expiresAt: 10.2, expired: .fault)
    let bench = BeltIntent(command: cmd, expiresAt: 10.2, expired: .off)
    XCTAssertEqual(live.current(now: 10.1), cmd)
    XCTAssertEqual(live.current(now: 10.2), .fault)
    XCTAssertEqual(bench.current(now: 10.2), .off)
    XCTAssertEqual(BeltIntent(command: .off, expiresAt: 10.2, expired: .fault).current(now: 10.2), .fault)
    XCTAssertEqual(BeltIntent.off.current(now: 10000), .off)
  }

  func testStuckWriteFailsAtOneSecondWithoutAQueuedReplacement() {
    var gate = BeltWriteGate()
    _ = gate.take(.off, now: 0)
    XCTAssertFalse(gate.timedOut(now: 0.99))
    XCTAssertTrue(gate.timedOut(now: 1))
    XCTAssertNil(gate.take(.fault, now: 1))
    XCTAssertEqual(gate.history.count, 1)
  }

  func testLateAckIsMeasuredAndRecoveryNeedsThreeTimelyWrites() {
    var gate = BeltWriteGate()
    _ = gate.take(.off, now: 0)
    gate.markUnreliable()
    gate.ack(now: 0.35)
    XCTAssertEqual(gate.lastAckMs!, 350, accuracy: 0.01)
    XCTAssertEqual(gate.healthyWrites, 0)
    for i in 1...3 {
      let t = 0.4 + Double(i) * 0.11
      XCTAssertNotNil(gate.take(.off, now: t))
      gate.ack(now: t + 0.02)
      XCTAssertEqual(gate.healthyWrites, i)
    }
    gate.markUnreliable()
    XCTAssertEqual(gate.healthyWrites, 0)
  }

  func testFirmwareStatusCanRecoverWithoutDisconnectButMustMatchNewCommand() {
    var gate = BeltWriteGate()
    var monitor = BeltStatusMonitor(startedAt: 0)
    _ = gate.take(.off, now: 0)
    monitor.receive(firmware(mode: 1, levels: [0, 0, 0, 0, 0]), gate: gate, now: 0.02)
    gate.ack(now: 0.03)
    monitor.receive(firmware(mode: 4), gate: gate, now: 0.4)
    XCTAssertNotNil(monitor.fault(now: 0.4))
    XCTAssertFalse(monitor.hasMatched(since: 0.4))
    _ = gate.take(.off, now: 0.41)
    monitor.receive(firmware(seq: 1, mode: 1, levels: [0, 0, 0, 0, 0]), gate: gate, now: 0.45)
    XCTAssertNil(monitor.fault(now: 0.45))
    XCTAssertTrue(monitor.hasMatched(since: 0.4))
    XCTAssertFalse(monitor.stalled(now: 3))
    XCTAssertTrue(monitor.stalled(now: 3.5))
  }
}
