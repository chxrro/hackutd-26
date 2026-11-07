// Checks the phone's side of docs/protocol.md against the shared golden vectors.
// Hostless logic tests: they compile the SDK-free files in HapticBelt/Hub directly,
// so they don't need the Meta SDK, glasses or a signed app.

import XCTest

final class ProtocolTests: XCTestCase {
  func testFramePacketMatchesGoldenVector() {
    let packet = FramePacket.encode(
      seq: 1, captureMs: 1_700_000_000_000, width: 504, height: 896, jpeg: Data([0xFF, 0xD8, 0xFF, 0xD9]))
    let golden: [UInt8] = [
      0x48, 0x42, 0x01, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x68, 0xE5, 0xCF,
      0x8B, 0x01, 0x00, 0x00, 0xF8, 0x01, 0x80, 0x03, 0xFF, 0xD8, 0xFF, 0xD9,
    ]
    XCTAssertEqual([UInt8](packet), golden)
    XCTAssertEqual(FramePacket.headerSize, 20)
  }

  func testParsesHubStatus() throws {
    let text = """
      {"type":"status","mode":"levels","healthy":true,"reason":null,"levels":[0,0,140,0,0],
       "belt":"connected","source":"glasses","inference_ms":85.2,"frame_age_ms":40.1}
      """
    let status = try XCTUnwrap(HubStatus.parse(text))
    XCTAssertEqual(status.mode, "levels")
    XCTAssertTrue(status.healthy)
    XCTAssertEqual(status.levels, [0, 0, 140, 0, 0])
    XCTAssertEqual(status.inferenceMs, 85.2)
  }

  func testRejectsNonStatusMessages() {
    XCTAssertNil(HubStatus.parse(#"{"type":"hello"}"#))
    XCTAssertNil(HubStatus.parse("not json"))
  }

  func testOutgoingMessages() throws {
    let hello = try JSONSerialization.jsonObject(with: Data(HubOutgoing.hello().utf8)) as? [String: Any]
    XCTAssertEqual(hello?["type"] as? String, "hello")
    let status = try JSONSerialization.jsonObject(with: Data(HubOutgoing.phoneStatus(stream: "streaming").utf8))
      as? [String: Any]
    XCTAssertEqual(status?["type"] as? String, "phone_status")
    XCTAssertEqual(status?["stream"] as? String, "streaming")
  }

  func testParseHubURL() {
    XCTAssertEqual(HubAddress.parse("192.168.1.20")?.absoluteString, "ws://192.168.1.20:8765")
    XCTAssertEqual(HubAddress.parse(" ws://10.0.0.5:9000 ")?.absoluteString, "ws://10.0.0.5:9000")
    XCTAssertNil(HubAddress.parse("http://10.0.0.5"))
    XCTAssertNil(HubAddress.parse(""))
  }
}
