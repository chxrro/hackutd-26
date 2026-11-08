// Route guidance logic (HapticBelt/Navigation). Hostless: synthetic fixes and the
// bundled ECSW -> Comets LANding GeoJSON read straight from the source tree.
// These are simulation results only; nothing here was walked.

import XCTest

final class RouteGuidanceTests: XCTestCase {
  // 100 m due north, then 100 m due east, around a point near ECSW.
  let origin = GeoPoint(lat: 32.9864, lon: -96.7511)
  lazy var corner = Geo.offset(origin, meters: 100, bearingDeg: 0)
  lazy var end = Geo.offset(corner, meters: 100, bearingDeg: 90)
  lazy var lRoute = Route(name: "L", destination: "End", attribution: "", points: [origin, corner, end])!

  func fix(_ p: GeoPoint, heading: Double?, acc: Double = 5, at t: Date = Date(timeIntervalSince1970: 1000))
    -> LocationFix
  {
    LocationFix(
      point: p, horizontalAccuracy: acc, heading: heading, headingAccuracy: heading == nil ? nil : 10,
      course: nil, speed: nil, timestamp: t)
  }

  let now = Date(timeIntervalSince1970: 1000)

  // MARK: Geometry

  func testDistanceAndBearing() {
    XCTAssertEqual(Geo.distance(origin, corner), 100, accuracy: 0.1)
    XCTAssertEqual(Geo.bearing(origin, corner), 0, accuracy: 0.01)
    XCTAssertEqual(Geo.bearing(corner, end), 90, accuracy: 0.1)
    XCTAssertEqual(Geo.normalize180(350), -10, accuracy: 1e-9)
    XCTAssertEqual(Geo.normalize180(190), -170, accuracy: 1e-9)
    XCTAssertEqual(lRoute.length, 200, accuracy: 0.2)
  }

  func testProjectionAndPointAt() {
    let side = Geo.offset(Geo.offset(origin, meters: 40, bearingDeg: 0), meters: 6, bearingDeg: 90)
    let p = lRoute.project(side)
    XCTAssertEqual(p.along, 40, accuracy: 0.2)
    XCTAssertEqual(p.crossTrack, 6, accuracy: 0.2)
    XCTAssertEqual(Geo.distance(lRoute.point(at: 150), Geo.offset(corner, meters: 50, bearingDeg: 90)), 0, accuracy: 0.2)
  }

  func testParsesGeoJSONAndDropsDuplicates() throws {
    let json = """
      {"type":"Feature","properties":{"name":"T","destination":"D","attribution":"A"},
       "geometry":{"type":"LineString","coordinates":[[-96.75,32.98],[-96.75,32.98],[-96.75,32.981]]}}
      """
    let r = try Route.parseGeoJSON(Data(json.utf8))
    XCTAssertEqual(r.points.count, 2)
    XCTAssertEqual(r.destination, "D")
    XCTAssertThrowsError(try Route.parseGeoJSON(Data(#"{"type":"Point","coordinates":[0,0]}"#.utf8)))
  }

  // MARK: Instructions

  func testFacingAlongRouteIsAhead() {
    var f = RouteFollower(route: lRoute)
    let g = f.update(fix(Geo.offset(origin, meters: 20, bearingDeg: 0), heading: 2), now: now)
    XCTAssertEqual(g.instruction, .ahead)
    XCTAssertEqual(g.distanceRemaining ?? 0, 180, accuracy: 1)
    XCTAssertFalse(g.offRoute)
  }

  func testLeftRightAndTurnAround() {
    let here = Geo.offset(origin, meters: 20, bearingDeg: 0)
    var f = RouteFollower(route: lRoute)
    XCTAssertEqual(f.update(fix(here, heading: 90), now: now).instruction, .left)  // facing east, route goes north
    f.reset()
    XCTAssertEqual(f.update(fix(here, heading: 270), now: now).instruction, .right)
    f.reset()
    XCTAssertEqual(f.update(fix(here, heading: 180), now: now).instruction, .turnAround)
    f.reset()
    XCTAssertEqual(f.update(fix(here, heading: 35), now: now).instruction, .slightLeft)
  }

  func testCornerTurnsRightAhead() {
    // 5 m before the corner the lookahead point is already 5 m along the eastward leg.
    var f = RouteFollower(route: lRoute)
    let g = f.update(fix(Geo.offset(origin, meters: 95, bearingDeg: 0), heading: 0), now: now)
    XCTAssertEqual(g.instruction, .slightRight)
  }

  func testHysteresisHoldsNearBoundary() {
    var f = RouteFollower(route: lRoute)
    let here = Geo.offset(origin, meters: 20, bearingDeg: 0)
    // Relative bearing = -heading here (route goes north). 15 deg -> ahead; 23 deg is just
    // over the 20 deg line but inside the 8 deg margin -> stays ahead; 30 -> slight left.
    XCTAssertEqual(f.update(fix(here, heading: 15), now: now).instruction, .ahead)
    XCTAssertEqual(f.update(fix(here, heading: 23), now: now).instruction, .ahead)
    XCTAssertEqual(f.update(fix(here, heading: 30), now: now).instruction, .slightLeft)
    XCTAssertEqual(f.update(fix(here, heading: 17), now: now).instruction, .slightLeft)  // inside margin
    XCTAssertEqual(f.update(fix(here, heading: 5), now: now).instruction, .ahead)
  }

  func testOffRouteAimsBackAtPath() {
    var f = RouteFollower(route: lRoute)
    // 30 m east of the north leg, facing north: path is to the left.
    let p = Geo.offset(Geo.offset(origin, meters: 50, bearingDeg: 0), meters: 30, bearingDeg: 90)
    let g = f.update(fix(p, heading: 0), now: now)
    XCTAssertTrue(g.offRoute)
    XCTAssertEqual(g.instruction, .left)
  }

  func testArrivalLatches() {
    var f = RouteFollower(route: lRoute)
    _ = f.update(fix(Geo.offset(corner, meters: 50, bearingDeg: 90), heading: 90), now: now)
    XCTAssertEqual(f.update(fix(Geo.offset(end, meters: 3, bearingDeg: 270), heading: 90), now: now).instruction, .arrived)
    // A later noisy fix doesn't un-arrive.
    XCTAssertEqual(f.update(fix(Geo.offset(end, meters: 20, bearingDeg: 270), heading: 90), now: now).instruction, .arrived)
  }

  // MARK: Gates

  func testNoGuidanceOnStaleOrUncertainFix() {
    var f = RouteFollower(route: lRoute)
    let here = Geo.offset(origin, meters: 20, bearingDeg: 0)
    XCTAssertEqual(f.update(nil, now: now).instruction, .unavailable("No location yet"))
    XCTAssertEqual(
      f.update(fix(here, heading: 0, at: now.addingTimeInterval(-6)), now: now).instruction,
      .unavailable("Location is stale"))
    XCTAssertEqual(f.update(fix(here, heading: 0, acc: 40), now: now).instruction, .unavailable("Location too uncertain"))
    XCTAssertEqual(f.update(fix(here, heading: 0, acc: -1), now: now).instruction, .unavailable("Location too uncertain"))
  }

  func testHeadingSources() {
    let here = Geo.offset(origin, meters: 20, bearingDeg: 0)
    var f = RouteFollower(route: lRoute)
    // No compass, standing still: no facing.
    XCTAssertEqual(f.update(fix(here, heading: nil), now: now).instruction, .noHeading)
    // Walking north at 1.2 m/s: course is used.
    var walking = fix(here, heading: nil)
    walking.course = 0
    walking.speed = 1.2
    let g = f.update(walking, now: now)
    XCTAssertEqual(g.instruction, .ahead)
    XCTAssertEqual(g.headingUsed, "walking")
    // Compass-only mode ignores course.
    f.headingSource = .compass
    XCTAssertEqual(f.update(walking, now: now).instruction, .noHeading)
    // A compass with poor accuracy isn't trusted.
    var badCompass = fix(here, heading: 0)
    badCompass.headingAccuracy = 60
    XCTAssertEqual(f.update(badCompass, now: now).instruction, .noHeading)
  }

  // MARK: Announcer

  func guidance(_ i: Instruction, remaining: Double = 120) -> Guidance {
    Guidance(
      instruction: i, relativeBearing: 0, distanceRemaining: remaining, crossTrack: 0, offRoute: false,
      progress: 0.5, target: nil, headingUsed: "compass")
  }

  func testAnnouncerWaitsForStableInstructionAndRepeats() {
    var a = RouteAnnouncer()
    let t0 = now
    XCTAssertNil(a.next(guidance(.left), destination: "X", hubAlerting: false, now: t0))
    XCTAssertEqual(
      a.next(guidance(.left), destination: "X", hubAlerting: false, now: t0 + 1.1),
      "Route: turn left, 120 metres to X.")
    XCTAssertNil(a.next(guidance(.left), destination: "X", hubAlerting: false, now: t0 + 5))
    XCTAssertNotNil(a.next(guidance(.left), destination: "X", hubAlerting: false, now: t0 + 21.2))
  }

  func testAnnouncerFlickerIsNotSpoken() {
    var a = RouteAnnouncer()
    var t = now
    for i in 0..<10 {
      let g = guidance(i.isMultiple(of: 2) ? .ahead : .slightLeft)
      XCTAssertNil(a.next(g, destination: "X", hubAlerting: false, now: t))
      t += 0.5
    }
  }

  func testHubAlertSilencesRouteAndHoldsOff() {
    var a = RouteAnnouncer()
    let t0 = now
    XCTAssertNil(a.next(guidance(.right), destination: "X", hubAlerting: false, now: t0))
    // Obstacle alert arrives just as the instruction becomes stable: nothing is said.
    XCTAssertNil(a.next(guidance(.right), destination: "X", hubAlerting: true, now: t0 + 1.2))
    XCTAssertNil(a.next(guidance(.right), destination: "X", hubAlerting: true, now: t0 + 3))
    // Alert clears at t0+3. Hold-off 1.5 s, then the instruction must be stable again for 1 s.
    XCTAssertNil(a.next(guidance(.right), destination: "X", hubAlerting: false, now: t0 + 4))
    XCTAssertNil(a.next(guidance(.right), destination: "X", hubAlerting: false, now: t0 + 4.6))
    XCTAssertNotNil(a.next(guidance(.right), destination: "X", hubAlerting: false, now: t0 + 5.7))
  }

  func testArrivalSpokenOnce() {
    var a = RouteAnnouncer()
    let g = guidance(.arrived, remaining: 0)
    // Held back while the hub alerts, then said at once (no stability wait), then never again.
    XCTAssertNil(a.next(g, destination: "X", hubAlerting: true, now: now))
    XCTAssertNil(a.next(g, destination: "X", hubAlerting: false, now: now + 1))
    XCTAssertEqual(
      a.next(g, destination: "X", hubAlerting: false, now: now + 1.6),
      "Route: near X. Confirm the entrance with your guide.")
    XCTAssertNil(a.next(g, destination: "X", hubAlerting: false, now: now + 40))
  }

  func testEveryPhraseIsMarkedAsRoute() {
    let all: [Instruction] = [
      .ahead, .slightLeft, .slightRight, .left, .right, .turnAround, .arrived, .noHeading, .unavailable("x"),
    ]
    for i in all {
      XCTAssertTrue(RouteAnnouncer.phrase(guidance(i), destination: "X").hasPrefix("Route"), "\(i)")
    }
  }

  func testHubGate() throws {
    func status(_ json: String) throws -> HubStatus { try XCTUnwrap(HubStatus.parse(json)) }
    let clear = try status(
      #"{"type":"status","mode":"levels","healthy":true,"reason":null,"levels":[0,0,0,0,0],"belt":"simulated"}"#)
    let obstacle = try status(
      #"{"type":"status","mode":"levels","healthy":true,"reason":null,"levels":[0,0,140,0,0],"belt":"simulated"}"#)
    let fault = try status(
      #"{"type":"status","mode":"fault","healthy":false,"reason":"stale frames","levels":[180,180,180,180,180],"belt":"simulated"}"#)
    XCTAssertFalse(HubGate.isAlerting(clear, fresh: true))
    XCTAssertTrue(HubGate.isAlerting(obstacle, fresh: true))
    XCTAssertTrue(HubGate.isAlerting(fault, fresh: true))
    XCTAssertFalse(HubGate.isAlerting(obstacle, fresh: false))
    XCTAssertFalse(HubGate.isAlerting(nil, fresh: true))
  }

  // MARK: Whole route, simulated

  func testSimulatedWalkReachesCometsLanding() throws {
    let url = URL(fileURLWithPath: #filePath)
      .deletingLastPathComponent().deletingLastPathComponent()
      .appendingPathComponent("HapticBelt/Navigation/ecsw-to-comets-landing.geojson")
    let route = try Route.parseGeoJSON(Data(contentsOf: url))
    XCTAssertEqual(route.destination, "Comets LANding")
    XCTAssertEqual(route.length, 320, accuracy: 15)  // the OSM scratch measurement was ~320 m

    for r in [route, route.reversed(name: "back", destination: "ECSW")] {
      var follower = RouteFollower(route: r)
      var announcer = RouteAnnouncer()
      var walker = SimulatedWalk(route: r)
      var t = now
      var g: Guidance?
      var phrases: [String] = []
      var maxOff = 0.0
      for _ in 0..<(4 * 60 * 10) {  // 10 simulated minutes at 4 Hz
        let fix = walker.step(dt: 0.25, toward: g?.target ?? r.points[1], now: t)
        g = follower.update(fix, now: t)
        maxOff = max(maxOff, g?.crossTrack ?? 0)
        if let p = announcer.next(g!, destination: r.destination, hubAlerting: false, now: t) { phrases.append(p) }
        if g?.instruction == .arrived { break }
        t += 0.25
      }
      XCTAssertEqual(g?.instruction, .arrived, "\(r.name): \(phrases)")
      XCTAssertTrue(phrases.first?.contains("turn around") ?? false, "starts facing away: \(phrases)")
      XCTAssertTrue(phrases.contains { $0.contains("near \(r.destination)") })
      XCTAssertGreaterThan(maxOff, 15, "the walker should have left the path: \(maxOff)")
      XCTAssertTrue(phrases.contains { $0.contains("off path") }, "\(phrases)")
      XCTAssertLessThan(maxOff, 35, "drift is corrected before it runs away")
      XCTAssertLessThan(t.timeIntervalSince(now), 6 * 60, "\(r.name) took too long")
    }
  }
}

