//
// SimulatedWalk.swift
//
// A deterministic pretend walker for the Simulator, tests, and a demo fallback.
// It starts facing the wrong way, follows the guidance after a short pause, and
// wanders off the path partway so the off-route recovery is exercised.
//
// Everything it produces is SIMULATED. The UI labels it so; never present a
// simulated walk as a real one.
//

import Foundation

struct SimulatedWalk: Sendable {
  var walkSpeed = 1.3  // m/s
  var turnRate = 60.0  // deg/s
  var gpsNoise = 3.0  // m, amplitude of the pseudo-random position wobble
  var reportedAccuracy = 6.0  // m
  /// Between these distances walked it ignores guidance and walks straight, this many
  /// degrees right of where it was facing, to leave the path and exercise "off path ... rejoin".
  var driftRange: ClosedRange<Double> = 60...95
  var driftDeg = 45.0
  /// Seconds it stands still at the start, as a person would until the first phrase.
  var reactionDelay = 2.5

  private(set) var position: GeoPoint
  private(set) var facing: Double
  private(set) var walked = 0.0
  private var t = 0.0
  private var driftHeading: Double?

  init(route: Route, startFacingOffset: Double = 160) {
    position = route.start
    // Face away from where guidance first aims (10 m along), not just the first
    // segment, which can be a few metres long and point elsewhere.
    facing = Geo.normalize360(Geo.bearing(route.start, route.point(at: 10)) + startFacingOffset)
  }

  /// Advance by dt seconds toward `target` (the guidance target), returning a fix.
  mutating func step(dt: Double, toward target: GeoPoint?, now: Date) -> LocationFix {
    t += dt
    if let target, t > reactionDelay {
      if driftRange.contains(walked) {
        // Lock a straight line off to the right, as someone missing a bend would.
        if driftHeading == nil { driftHeading = facing + driftDeg }
      } else {
        driftHeading = nil
      }
      let want = driftHeading ?? Geo.bearing(position, target)
      let err = Geo.normalize180(want - facing)
      let maxTurn = turnRate * dt
      facing = Geo.normalize360(facing + max(-maxTurn, min(maxTurn, err)))
      // Turn on the spot when far off, walk when roughly facing the target.
      let speed = abs(err) < 50 ? walkSpeed : 0.2
      position = Geo.offset(position, meters: speed * dt, bearingDeg: facing)
      walked += speed * dt
    }
    // Smooth, repeatable GPS wobble (no randomness, so tests are stable).
    let n = Geo.offset(
      Geo.offset(position, meters: gpsNoise * sin(t * 0.37), bearingDeg: 90),
      meters: gpsNoise * cos(t * 0.23), bearingDeg: 0)
    return LocationFix(
      point: n, horizontalAccuracy: reportedAccuracy,
      heading: Geo.normalize360(facing + 4 * sin(t * 1.3)), headingAccuracy: 15,
      course: walked > 0 ? facing : nil, speed: walkSpeed, timestamp: now)
  }
}
