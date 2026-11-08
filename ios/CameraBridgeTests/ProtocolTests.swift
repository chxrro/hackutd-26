// Checks the phone's side of docs/protocol.md (sections 1, 2 and the Control
// draft) against the documented examples and golden vectors. Hostless logic
// tests: they compile the SDK-free files directly, so they need no glasses,
// Meta SDK or signed app.

import XCTest

final class ProtocolTests: XCTestCase {
  // MARK: Section 1: frames

  func testFramePacketMatchesGoldenVector() {
    let packet = FramePacket.encode(
      seq: 1, captureMs: 1_700_000_000_000, width: 504, height: 896, jpeg: Data([0xFF, 0xD8, 0xFF, 0xD9]))
    // docs/protocol.md: 48 42 01 00 01 00 00 00 00 68 e5 cf 8b 01 00 00 f8 01 80 03 ff d8 ff d9
    let golden: [UInt8] = [
      0x48, 0x42, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x68, 0xE5, 0xCF,
      0x8B, 0x01, 0x00, 0x00, 0xF8, 0x01, 0x80, 0x03, 0xFF, 0xD8, 0xFF, 0xD9,
    ]
    XCTAssertEqual([UInt8](packet), golden)
    XCTAssertEqual(FramePacket.headerSize, 20)
  }

  func testFrameSeqWrapsLittleEndian() {
    let packet = [UInt8](FramePacket.encode(seq: .max, captureMs: 0, width: 1, height: 2, jpeg: Data([0xAA])))
    XCTAssertEqual(Array(packet[4..<8]), [0xFF, 0xFF, 0xFF, 0xFF])
    XCTAssertEqual(Array(packet[16..<20]), [0x01, 0x00, 0x02, 0x00])
    XCTAssertEqual(packet.last, 0xAA)
  }

  // MARK: Phone -> hub text

  func testHelloWithoutCodeMatchesDoc() throws {
    XCTAssertEqual(HubOutgoing.hello(), #"{"client":"ios","type":"hello","version":1}"#)
    try assertSameJSON(HubOutgoing.hello(), #"{"type": "hello", "client": "ios", "version": 1}"#)
  }

  func testHelloWithControlCodeMatchesDoc() throws {
    let hello = HubOutgoing.hello(controlCode: "3f9c0a1b2d4e5f60")
    XCTAssertEqual(hello, #"{"client":"ios","control_code":"3f9c0a1b2d4e5f60","type":"hello","version":1}"#)
    try assertSameJSON(
      hello, #"{"type": "hello", "client": "ios", "version": 1, "control_code": "3f9c0a1b2d4e5f60"}"#)
  }

  func testPhoneStatusMatchesDoc() throws {
    try assertSameJSON(
      HubOutgoing.phoneStatus(stream: "streaming"), #"{"type": "phone_status", "stream": "streaming"}"#)
  }

  func testPhoneStatusWithFrameSourceMatchesDoc() throws {
    let text = HubOutgoing.phoneStatus(stream: "streaming", frameSource: .mock)
    XCTAssertEqual(text, #"{"frame_source":"mock","stream":"streaming","type":"phone_status"}"#)
    try assertSameJSON(text, #"{"type": "phone_status", "stream": "streaming", "frame_source": "mock"}"#)
  }

  func testFrameSourceWireValuesMatchDoc() {
    // docs/protocol.md section 1: glasses | mock | test_pattern
    XCTAssertEqual(HubFrameSource.allCases.map(\.rawValue), ["glasses", "mock", "test_pattern"])
    XCTAssertTrue(HubOutgoing.phoneStatus(stream: "starting", frameSource: .testPattern)
      .contains(#""frame_source":"test_pattern""#))
  }

  func testFrameSourceFromTheRunsSource() {
    XCTAssertEqual(HubFrameSource(source: .glasses, mockGlasses: false), .glasses)
    XCTAssertEqual(HubFrameSource(source: .glasses, mockGlasses: true), .mock)
    // The test pattern is never the glasses, mock or not.
    XCTAssertEqual(HubFrameSource(source: .test, mockGlasses: false), .testPattern)
    XCTAssertEqual(HubFrameSource(source: .test, mockGlasses: true), .testPattern)
  }

  func testControlSetMatchesDoc() throws {
    let text = HubOutgoing.control(id: 12, .set(SettingsChange(alertRange: .far)))
    XCTAssertEqual(text, #"{"action":"set","id":12,"settings":{"alert_range":"far"},"type":"control"}"#)
    try assertSameJSON(
      text, #"{"type": "control", "id": 12, "action": "set", "settings": {"alert_range": "far"}}"#)
  }

  func testControlActionsHaveNoSettingsKey() {
    XCTAssertEqual(HubOutgoing.control(id: 0, .get), #"{"action":"get","id":0,"type":"control"}"#)
    XCTAssertEqual(HubOutgoing.control(id: 1, .pause), #"{"action":"pause","id":1,"type":"control"}"#)
    XCTAssertEqual(HubOutgoing.control(id: 2, .resume), #"{"action":"resume","id":2,"type":"control"}"#)
    XCTAssertEqual(HubOutgoing.control(id: 3, .beltTest), #"{"action":"belt_test","id":3,"type":"control"}"#)
    XCTAssertEqual(HubOutgoing.control(id: 4, .practiceCue(.fault)),
                   #"{"action":"practice_cue","cue":"fault","id":4,"type":"control"}"#)
  }

  func testEndSessionMatchesDoc() throws {
    let text = HubOutgoing.control(id: 7, .endSession)
    XCTAssertEqual(text, #"{"action":"end_session","id":7,"type":"control"}"#)
    try assertSameJSON(text, #"{"type": "control", "id": 7, "action": "end_session"}"#)
  }

  func testParsesStoppedStatusLeniently() throws {
    let base = #""type":"status","levels":[0,0,0,0,0],"belt":"connected""#
    let stopped = try XCTUnwrap(HubStatus.parse(
      #"{\#(base),"mode":"off","healthy":false,"reason":"stopped by wearer","stopped":true,"paused":false}"#))
    XCTAssertEqual(stopped.stopped, true)
    XCTAssertFalse(stopped.healthy)
    XCTAssertNil(try XCTUnwrap(HubStatus.parse(#"{\#(base),"mode":"levels","healthy":true}"#)).stopped)  // older hub
    XCTAssertNil(try XCTUnwrap(HubStatus.parse(#"{\#(base),"mode":"levels","healthy":true,"stopped":"yes"}"#)).stopped)
  }

  /// After end_session the client sends no frames until the next run's "starting".
  func testHubClientHoldsFramesFromEndSessionUntilStarting() async {
    let client = HubClient()
    var held = await client.framesHeld
    XCTAssertFalse(held)
    _ = await client.sendControl(id: 1, .pause)
    held = await client.framesHeld
    XCTAssertFalse(held)
    _ = await client.sendControl(id: 2, .endSession)  // not connected: not sent, but frames are held anyway
    held = await client.framesHeld
    XCTAssertTrue(held)
    await client.reportPhoneStatus(stream: "stopped", frameSource: .glasses)
    held = await client.framesHeld
    XCTAssertTrue(held)
    await client.reportPhoneStatus(stream: "starting", frameSource: .glasses)
    held = await client.framesHeld
    XCTAssertFalse(held)
  }

  func testControlSetEncodesEveryKnownSetting() throws {
    let text = HubOutgoing.control(id: 4, .set(SettingsChange(classifier: .nim, alertRange: .near, record: true)))
    try assertSameJSON(
      text,
      #"{"type":"control","id":4,"action":"set","settings":{"classifier":"nim","alert_range":"near","record":true}}"#)
  }

  // MARK: Hub -> phone

  func testParsesBaseStatusFromDoc() throws {
    let text = """
      {"type": "status", "mode": "levels", "healthy": true, "reason": null, "levels": [0, 0, 140, 0, 0],
       "belt": "connected", "inference_ms": 85.2, "frame_age_ms": 40.1, "frame_source": "glasses"}
      """
    let status = try XCTUnwrap(HubStatus.parse(text))
    XCTAssertEqual(status.mode, "levels")
    XCTAssertTrue(status.healthy)
    XCTAssertNil(status.reason)
    XCTAssertEqual(status.levels, [0, 0, 140, 0, 0])
    XCTAssertEqual(status.belt, "connected")
    XCTAssertEqual(status.inferenceMs, 85.2)
    XCTAssertEqual(status.frameAgeMs, 40.1)
    XCTAssertNil(status.paused)
    XCTAssertNil(status.settings)
    XCTAssertNil(status.sectors)
    XCTAssertFalse(status.isPaused)
  }

  func testParsesStatusControlAdditionsFromDoc() throws {
    let text = """
      {"type": "status", "mode": "levels", "healthy": true, "reason": null, "levels": [0, 90, 140, 0, 0],
       "belt": "connected", "paused": false,
       "settings": {"classifier": "local", "alert_range": "normal", "record": false, "paused": false},
       "sectors": [{"kind": null}, {"kind": "object"}, {"kind": "person"}, {"kind": null}, {"kind": null}]}
      """
    let status = try XCTUnwrap(HubStatus.parse(text))
    XCTAssertEqual(status.paused, false)
    XCTAssertEqual(status.settings, HubSettings(classifier: "local", alertRange: "normal", record: false, paused: false))
    XCTAssertEqual(status.sectors?.map(\.kind), [nil, "object", "person", nil, nil])
  }

  func testIgnoresUnknownStatusFields() throws {
    let text = """
      {"type":"status","mode":"fault","healthy":false,"reason":"no camera frames for 1.2 s","levels":[0,0,0,0,0],
       "belt":"simulated","source":"glasses","future_field":{"x":[1,2]},"motors":5,
       "settings":{"classifier":"local","alert_range":"far","record":false,"paused":false,"new_setting":"x"},
       "sectors":[{"kind":"person","distance_m":2.0},{"kind":null},{"kind":null},{"kind":null},{"kind":"vehicle"}]}
      """
    let status = try XCTUnwrap(HubStatus.parse(text))
    XCTAssertEqual(status.mode, "fault")
    XCTAssertFalse(status.healthy)
    XCTAssertEqual(status.reason, "no camera frames for 1.2 s")
    XCTAssertEqual(status.source, "glasses")
    XCTAssertEqual(status.settings?.alertRange, "far")
    XCTAssertEqual(status.sectors?.first?.kind, "person")
    XCTAssertEqual(status.sectors?.last?.kind, "vehicle")
  }

  func testMalformedDraftFieldsDontDropTheStatus() throws {
    let text = """
      {"type":"status","mode":"levels","healthy":true,"reason":null,"levels":[0,0,0,0,0],"belt":"connected",
       "paused":"maybe","settings":[1,2],"sectors":{"kind":"person"}}
      """
    let status = try XCTUnwrap(HubStatus.parse(text))
    XCTAssertNil(status.paused)
    XCTAssertNil(status.settings)
    XCTAssertNil(status.sectors)
  }

  func testPausedComesFromThePausedFieldNotTheReason() throws {
    let base = #""type":"status","mode":"off","levels":[0,0,0,0,0],"belt":"connected""#
    // A fault takes priority over "alerts paused" in reason: paused is still true.
    let faultWhilePaused = try XCTUnwrap(HubStatus.parse(
      #"{\#(base),"healthy":false,"reason":"no camera frames for 1.2 s","paused":true}"#))
    let viaSettings = try XCTUnwrap(HubStatus.parse(#"{\#(base),"healthy":false,"reason":"x","settings":{"paused":true}}"#))
    let reasonOnly = try XCTUnwrap(HubStatus.parse(#"{\#(base),"healthy":false,"reason":"alerts paused"}"#))
    let notPaused = try XCTUnwrap(HubStatus.parse(#"{\#(base),"healthy":false,"reason":"alerts paused","paused":false}"#))
    XCTAssertTrue(faultWhilePaused.isPaused)
    XCTAssertTrue(viaSettings.isPaused)
    XCTAssertFalse(reasonOnly.isPaused)
    XCTAssertFalse(notPaused.isPaused)
  }

  func testBeltTestStatusFromDoc() throws {
    let status = try XCTUnwrap(HubStatus.parse(
      #"{"type":"status","mode":"levels","healthy":false,"reason":"belt test","levels":[255,0,0,0,0],"belt":"connected","paused":false}"#))
    XCTAssertTrue(status.isBeltTest)
    XCTAssertFalse(status.isPaused)
  }

  func testRejectsNonStatusMessages() {
    XCTAssertNil(HubStatus.parse(#"{"type":"hello"}"#))
    XCTAssertNil(HubStatus.parse("not json"))
    XCTAssertNil(HubStatus.parse(#"{"type":"status","mode":"levels"}"#))  // missing required fields
  }

  func testParsesAckFromDoc() throws {
    let text = """
      {"type": "ack", "id": 12, "ok": true, "error": null, "settings": {"classifier": "local", "alert_range": "far", "record": false, "paused": false}}
      """
    guard case .ack(let ack)? = HubIncoming.parse(text) else { return XCTFail("not an ack") }
    XCTAssertEqual(ack, HubAck(id: 12, ok: true, error: nil,
                               settings: HubSettings(classifier: "local", alertRange: "far", record: false, paused: false)))
  }

  func testAckWithNullIdAndUnparsedErrors() throws {
    guard case .ack(let ack)? = HubIncoming.parse(
      #"{"type":"ack","id":null,"ok":false,"error":"unknown setting: volume","settings":{"record":false}}"#)
    else { return XCTFail("not an ack") }
    XCTAssertNil(ack.id)
    XCTAssertNotEqual(ack.error, HubAck.notPaired)
    guard case .ack(let refused)? = HubIncoming.parse(
      #"{"type":"ack","id":7,"ok":false,"error":"recording not available","settings":{"record":false}}"#)
    else { return XCTFail("not an ack") }
    XCTAssertEqual(refused.settings?.record, false)
  }

  func testParsesRefusedAck() throws {
    let text = #"{"type":"ack","id":3,"ok":false,"error":"not paired","settings":{"classifier":"local","alert_range":"normal","record":false,"paused":false},"extra":1}"#
    guard case .ack(let ack)? = HubIncoming.parse(text) else { return XCTFail("not an ack") }
    XCTAssertEqual(ack.id, 3)
    XCTAssertFalse(ack.ok)
    XCTAssertEqual(ack.error, HubAck.notPaired)
  }

  func testIncomingRoutesByTypeAndIgnoresOthers() {
    if case .status? = HubIncoming.parse(
      #"{"type":"status","mode":"levels","healthy":true,"levels":[0],"belt":"connected"}"#) {} else {
      XCTFail("status not routed")
    }
    XCTAssertNil(HubIncoming.parse(#"{"type":"dashboard","x":1}"#))
    XCTAssertNil(HubIncoming.parse(#"{"no_type":true}"#))
    XCTAssertNil(HubIncoming.parse("[]"))
  }

  // MARK: Helpers

  private func assertSameJSON(_ actual: String, _ expected: String, file: StaticString = #filePath, line: UInt = #line) throws {
    let a = try JSONSerialization.jsonObject(with: Data(actual.utf8)) as? NSDictionary
    let e = try JSONSerialization.jsonObject(with: Data(expected.utf8)) as? NSDictionary
    XCTAssertNotNil(a, file: file, line: line)
    XCTAssertEqual(a, e, file: file, line: line)
  }
}
