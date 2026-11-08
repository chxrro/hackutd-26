import XCTest

final class PairingTests: XCTestCase {
  private func url(_ address: String, _ code: String = "") -> String? {
    try? HubPairing.validate(address: address, code: code).get().url.absoluteString
  }

  func testAcceptsLocalHubAddresses() {
    XCTAssertEqual(url("192.168.1.20"), "ws://192.168.1.20:8765/")
    XCTAssertEqual(url(" 192.168.1.20:9000 "), "ws://192.168.1.20:9000/")
    XCTAssertEqual(url("ws://10.0.0.5:8765"), "ws://10.0.0.5:8765/")
    XCTAssertEqual(url("ws://10.0.0.5:8765/"), "ws://10.0.0.5:8765/")
    XCTAssertEqual(url("Laptop.local"), "ws://laptop.local:8765/")
    XCTAssertEqual(url("172.16.0.1"), "ws://172.16.0.1:8765/")
    XCTAssertEqual(url("127.0.0.1"), "ws://127.0.0.1:8765/")
  }

  func testRejectsNonLocalOrMalformedAddresses() {
    XCTAssertEqual(HubPairing.validate(address: "", code: "").failure, .invalidAddress)
    XCTAssertEqual(HubPairing.validate(address: "http://192.168.1.20:8765", code: "").failure, .invalidAddress)
    XCTAssertEqual(HubPairing.validate(address: "wss://192.168.1.20", code: "").failure, .invalidAddress)
    XCTAssertEqual(HubPairing.validate(address: "ws://192.168.1.20/path", code: "").failure, .invalidAddress)
    XCTAssertEqual(HubPairing.validate(address: "ws://user:pw@192.168.1.20", code: "").failure, .invalidAddress)
    XCTAssertEqual(HubPairing.validate(address: "ws://192.168.1.20?x=1", code: "").failure, .invalidAddress)
    XCTAssertEqual(HubPairing.validate(address: "8.8.8.8", code: "").failure, .notLocal)
    XCTAssertEqual(HubPairing.validate(address: "example.com", code: "").failure, .notLocal)
    XCTAssertEqual(HubPairing.validate(address: "172.32.0.1", code: "").failure, .notLocal)
    XCTAssertEqual(HubPairing.validate(address: "192.168.1.020", code: "").failure, .notLocal)
  }

  func testControlCodeIsOptionalButMustBe16Hex() throws {
    XCTAssertNil(try HubPairing.validate(address: "192.168.1.20", code: "  ").get().controlCode)
    XCTAssertEqual(
      try HubPairing.validate(address: "192.168.1.20", code: " 3F9C0A1B2D4E5F60 ").get().controlCode,
      "3f9c0a1b2d4e5f60")
    for bad in ["3f9c0a1b2d4e5f6", "3f9c0a1b2d4e5f601", "3f9c0a1b2d4e5f6g", "3f9c 0a1b2d4e5f6"] {
      XCTAssertEqual(HubPairing.validate(address: "192.168.1.20", code: bad).failure, .invalidCode, bad)
    }
  }

  func testSharedRulesMatchEthansViewerUploader() {
    XCTAssertTrue(LocalNetwork.isLocalHost("mac.local"))
    XCTAssertTrue(LocalNetwork.isLocalHost("10.1.2.3"))
    XCTAssertFalse(LocalNetwork.isLocalHost("1.2.3.4"))
    XCTAssertFalse(LocalNetwork.isLocalHost("10.1.2"))
    XCTAssertNil(LocalNetwork.normalizedCode(""))
    XCTAssertEqual(LocalNetwork.normalizedCode("ABCDEF0123456789"), "abcdef0123456789")

    // The viewer uploader keeps its own http:// + explicit port requirement.
    XCTAssertNoThrow(try FrameUploader(address: "http://127.0.0.1:8765", code: "abcdef0123456789", source: .test))
    XCTAssertThrowsError(try FrameUploader(address: "http://127.0.0.1", code: "abcdef0123456789", source: .test))
    XCTAssertThrowsError(try FrameUploader(address: "http://8.8.8.8:8765", code: "abcdef0123456789", source: .test))
    XCTAssertThrowsError(try FrameUploader(address: "http://127.0.0.1:8765", code: "short", source: .test))
    XCTAssertEqual(
      try FrameUploader(address: "http://mac.local:8765", code: "abcdef0123456789", source: .glasses).endpoint
        .absoluteString,
      "http://mac.local:8765/frame")
  }
}

extension Result {
  fileprivate var failure: Failure? {
    if case .failure(let error) = self { return error }
    return nil
  }
}
