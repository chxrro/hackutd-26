import Foundation
import os
import XCTest

final class BeltTransportTests: XCTestCase {
  @MainActor
  func testHeartbeatContinuesWhileMainThreadIsBlocked() {
    // No scan is requested, so this does not create a Bluetooth manager or
    // require a radio. It exercises the same queue/timer as the real writer.
    let observations = OSAllocatedUnfairLock(initialState: [Bool]())
    let transport = BeltTransport { _, _ in
      observations.withLock { $0.append(Thread.isMainThread) }
    }
    withExtendedLifetime(transport) {
      Thread.sleep(forTimeInterval: 0.75)
      let callbacks = observations.withLock { $0 }
      XCTAssertGreaterThanOrEqual(callbacks.count, 2,
        "The heartbeat must execute even when the UI cannot process callbacks")
      XCTAssertTrue(callbacks.allSatisfy { !$0 })
    }
  }
}
