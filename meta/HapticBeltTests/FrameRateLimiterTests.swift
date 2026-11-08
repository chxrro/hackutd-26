import XCTest

final class FrameRateLimiterTests: XCTestCase {
  func testCameraTimingJitterDoesNotDiscardHalfTheFrames() {
    let start = ContinuousClock.now
    var limiter = FrameRateLimiter(framesPerSecond: 15)
    // Nominal 15 fps callbacks alternate early and late by 5 ms.
    for i in 0..<150 {
      let seconds = Double(i) / 15 + (i.isMultiple(of: 2) ? 0 : -0.005)
      XCTAssertTrue(limiter.allow(at: start.advanced(by: .seconds(seconds))), "frame \(i)")
    }
  }

  func testFastInputIsBoundedBySustainedRateAndTwoFrameBurst() {
    let start = ContinuousClock.now
    var limiter = FrameRateLimiter(framesPerSecond: 15)
    var accepted = 0
    for millisecond in 0..<10_000 {
      if limiter.allow(at: start.advanced(by: .milliseconds(millisecond))) { accepted += 1 }
    }
    XCTAssertGreaterThanOrEqual(accepted, 149)
    XCTAssertLessThanOrEqual(accepted, 152)
  }

  func testIdleTimeCannotAccumulateAnUnboundedBurst() {
    let start = ContinuousClock.now
    var limiter = FrameRateLimiter(framesPerSecond: 15)
    XCTAssertTrue(limiter.allow(at: start))
    let later = start.advanced(by: .seconds(60))
    XCTAssertTrue(limiter.allow(at: later))
    XCTAssertTrue(limiter.allow(at: later))
    XCTAssertFalse(limiter.allow(at: later))
  }
}
