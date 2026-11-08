//
// RouteGuidance.swift
//
// Pure route-following logic for the experimental campus route (no CoreLocation,
// no UI), so the hostless tests can drive it with synthetic fixes.
//
//   Route      a walking polyline loaded from GeoJSON (OSM today; UTD's map export later)
//   RouteFollower.update(fix) -> Guidance   where am I on the route, which way to face
//   RouteAnnouncer.next(...)  -> phrase?    when to say something, and when to stay quiet
//
// Guidance is a suggestion for a supervised demo, never proof that a direction is
// safe. It says nothing about obstacles; the hub's obstacle alerts and faults always
// win (see RouteAnnouncer and docs/navigation-research.md §3).
//

import Foundation

// MARK: - Geometry

struct GeoPoint: Equatable, Sendable {
  var lat: Double
  var lon: Double
}

enum Geo {
  static let earthRadiusM = 6_371_000.0

  /// Great-circle distance in metres.
  static func distance(_ a: GeoPoint, _ b: GeoPoint) -> Double {
    let dLat = (b.lat - a.lat) * .pi / 180
    let dLon = (b.lon - a.lon) * .pi / 180
    let h =
      sin(dLat / 2) * sin(dLat / 2)
      + cos(a.lat * .pi / 180) * cos(b.lat * .pi / 180) * sin(dLon / 2) * sin(dLon / 2)
    return 2 * earthRadiusM * asin(min(1, sqrt(h)))
  }

  /// Initial bearing from a to b, degrees clockwise from true north, in [0, 360).
  static func bearing(_ a: GeoPoint, _ b: GeoPoint) -> Double {
    let lat1 = a.lat * .pi / 180
    let lat2 = b.lat * .pi / 180
    let dLon = (b.lon - a.lon) * .pi / 180
    let y = sin(dLon) * cos(lat2)
    let x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLon)
    return normalize360(atan2(y, x) * 180 / .pi)
  }

  /// Point `meters` away from p along `bearingDeg`.
  static func offset(_ p: GeoPoint, meters: Double, bearingDeg: Double) -> GeoPoint {
    let d = meters / earthRadiusM
    let b = bearingDeg * .pi / 180
    let lat1 = p.lat * .pi / 180
    let lon1 = p.lon * .pi / 180
    let lat2 = asin(sin(lat1) * cos(d) + cos(lat1) * sin(d) * cos(b))
    let lon2 = lon1 + atan2(sin(b) * sin(d) * cos(lat1), cos(d) - sin(lat1) * sin(lat2))
    return GeoPoint(lat: lat2 * 180 / .pi, lon: lon2 * 180 / .pi)
  }

  static func normalize360(_ deg: Double) -> Double {
    let r = deg.truncatingRemainder(dividingBy: 360)
    return r < 0 ? r + 360 : r
  }

  /// Signed angle in [-180, 180): negative = to the left.
  static func normalize180(_ deg: Double) -> Double {
    let r = normalize360(deg + 180) - 180
    return r
  }
}

/// Flat east/north metres around an origin. Good to well under a metre over a campus.
private struct LocalFrame {
  let origin: GeoPoint
  let mPerDegLat = Geo.earthRadiusM * .pi / 180
  let mPerDegLon: Double

  init(origin: GeoPoint) {
    self.origin = origin
    mPerDegLon = Geo.earthRadiusM * .pi / 180 * cos(origin.lat * .pi / 180)
  }

  func xy(_ p: GeoPoint) -> (x: Double, y: Double) {
    ((p.lon - origin.lon) * mPerDegLon, (p.lat - origin.lat) * mPerDegLat)
  }

  func geo(x: Double, y: Double) -> GeoPoint {
    GeoPoint(lat: origin.lat + y / mPerDegLat, lon: origin.lon + x / mPerDegLon)
  }
}

// MARK: - Route

struct Route: Sendable {
  let name: String
  let destination: String
  let attribution: String
  let points: [GeoPoint]
  /// Distance along the route at each point, metres. cumulative[0] == 0.
  let cumulative: [Double]

  var length: Double { cumulative.last ?? 0 }
  var start: GeoPoint { points[0] }
  var end: GeoPoint { points[points.count - 1] }

  init?(name: String, destination: String, attribution: String, points raw: [GeoPoint]) {
    // Drop consecutive duplicates; they make zero-length segments.
    var pts: [GeoPoint] = []
    for p in raw where pts.last.map({ Geo.distance($0, p) > 0.05 }) ?? true { pts.append(p) }
    guard pts.count >= 2 else { return nil }
    var cum: [Double] = [0]
    for i in 1..<pts.count { cum.append(cum[i - 1] + Geo.distance(pts[i - 1], pts[i])) }
    self.name = name
    self.destination = destination
    self.attribution = attribution
    self.points = pts
    self.cumulative = cum
  }

  /// The same path walked the other way.
  func reversed(name: String, destination: String) -> Route {
    Route(name: name, destination: destination, attribution: attribution, points: points.reversed())!
  }

  /// Point at distance s along the route (clamped to the ends).
  func point(at s: Double) -> GeoPoint {
    if s <= 0 { return start }
    if s >= length { return end }
    var i = 1
    while i < cumulative.count - 1 && cumulative[i] < s { i += 1 }
    let segLen = cumulative[i] - cumulative[i - 1]
    let t = segLen > 0 ? (s - cumulative[i - 1]) / segLen : 0
    let a = points[i - 1]
    let b = points[i]
    return GeoPoint(lat: a.lat + (b.lat - a.lat) * t, lon: a.lon + (b.lon - a.lon) * t)
  }

  struct Projection: Equatable {
    var along: Double  // metres from the start
    var crossTrack: Double  // metres from the polyline
    var point: GeoPoint
  }

  /// Closest point on the route to p, searching only segments that overlap
  /// [minAlong, maxAlong] so a noisy fix can't jump to a far part of a looping path.
  func project(_ p: GeoPoint, minAlong: Double = -.infinity, maxAlong: Double = .infinity) -> Projection {
    let frame = LocalFrame(origin: p)
    var best = Projection(along: 0, crossTrack: .infinity, point: start)
    for i in 1..<points.count {
      if cumulative[i] < minAlong || cumulative[i - 1] > maxAlong { continue }
      let a = frame.xy(points[i - 1])
      let b = frame.xy(points[i])
      let dx = b.x - a.x
      let dy = b.y - a.y
      let len2 = dx * dx + dy * dy
      var t = len2 > 0 ? ((0 - a.x) * dx + (0 - a.y) * dy) / len2 : 0
      t = max(0, min(1, t))
      let cx = a.x + t * dx
      let cy = a.y + t * dy
      let d = (cx * cx + cy * cy).squareRoot()
      if d < best.crossTrack {
        let along = cumulative[i - 1] + t * (cumulative[i] - cumulative[i - 1])
        best = Projection(along: along, crossTrack: d, point: frame.geo(x: cx, y: cy))
      }
    }
    return best
  }
}

extension Route {
  enum ParseError: Error { case notGeoJSON, noLineString }

  /// Reads the first LineString (or Feature/FeatureCollection holding one) from GeoJSON.
  /// Properties used if present: name, destination, attribution.
  static func parseGeoJSON(_ data: Data, fallbackName: String = "Route") throws -> Route {
    guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
      throw ParseError.notGeoJSON
    }
    func lineString(_ obj: [String: Any]) -> ([[Double]], [String: Any])? {
      switch obj["type"] as? String {
      case "LineString":
        return (obj["coordinates"] as? [[Double]]).map { ($0, [:]) }
      case "Feature":
        guard let g = obj["geometry"] as? [String: Any], let (c, _) = lineString(g) else { return nil }
        return (c, obj["properties"] as? [String: Any] ?? [:])
      case "FeatureCollection":
        for f in obj["features"] as? [[String: Any]] ?? [] {
          if let found = lineString(f) { return found }
        }
        return nil
      default:
        return nil
      }
    }
    guard let (coords, props) = lineString(root) else { throw ParseError.noLineString }
    // GeoJSON order is [lon, lat].
    let pts = coords.compactMap { $0.count >= 2 ? GeoPoint(lat: $0[1], lon: $0[0]) : nil }
    guard
      let route = Route(
        name: props["name"] as? String ?? fallbackName,
        destination: props["destination"] as? String ?? "destination",
        attribution: props["attribution"] as? String ?? "",
        points: pts)
    else { throw ParseError.noLineString }
    return route
  }
}

// MARK: - Fixes and guidance

/// One location reading. Heading and course are degrees from true north; nil when unknown.
struct LocationFix: Sendable {
  var point: GeoPoint
  var horizontalAccuracy: Double  // metres, 1 sigma-ish (CoreLocation semantics)
  var heading: Double?  // which way the phone faces (compass)
  var headingAccuracy: Double?
  var course: Double?  // which way the wearer is moving (GPS)
  var speed: Double?  // m/s
  var timestamp: Date
}

enum HeadingSource: String, CaseIterable, Sendable {
  /// Compass while it's trustworthy, walking direction otherwise.
  case automatic
  /// Phone compass only (phone held flat, top pointing forward).
  case compass
  /// GPS walking direction only (phone in a pocket or on a lanyard). Needs steady walking.
  case walking
}

enum Instruction: Equatable, Sendable {
  case ahead
  case slightLeft, slightRight
  case left, right
  case turnAround
  case arrived
  /// Position known but no trustworthy facing direction yet.
  case noHeading
  /// Can't guide at all: no fix, stale fix, or accuracy too poor.
  case unavailable(String)

  /// How big a turn this is (0 ahead ... 3 turn around), for hysteresis. nil: not a direction.
  fileprivate var magnitude: Int? {
    switch self {
    case .ahead: return 0
    case .slightLeft, .slightRight: return 1
    case .left, .right: return 2
    case .turnAround: return 3
    default: return nil
    }
  }
}

struct Guidance: Equatable, Sendable {
  var instruction: Instruction
  /// Where to face relative to the current facing, degrees; negative = left. nil if unknown.
  var relativeBearing: Double?
  var distanceRemaining: Double?
  var crossTrack: Double?
  var offRoute: Bool
  var progress: Double  // 0...1
  var target: GeoPoint?
  var headingUsed: String  // "compass" | "walking" | "none"
}

struct GuidanceConfig: Sendable {
  var lookahead = 10.0  // m: aim this far ahead along the route (smooths GPS noise)
  var arriveRadius = 8.0  // m
  var offRouteDistance = 15.0  // m from the path (or the fix's accuracy, if worse)
  var maxHorizontalAccuracy = 25.0  // m: worse than this, don't guide
  var maxFixAge = 5.0  // s
  var maxHeadingAccuracy = 35.0  // deg
  var minWalkingSpeed = 0.5  // m/s: below this GPS course is noise
  var aheadDeg = 20.0  // |relative| below this: straight on
  var slightDeg = 55.0  // below this: bear left/right
  var turnDeg = 140.0  // below this: turn left/right; above: turn around
  var hysteresisDeg = 8.0
  /// How far back/forward of the last progress a fix may snap to.
  var backtrackWindow = 25.0
  var forwardWindow = 80.0
}

struct RouteFollower: Sendable {
  let route: Route
  var config = GuidanceConfig()
  var headingSource: HeadingSource = .automatic
  private(set) var along: Double?
  private(set) var arrived = false
  private var lastInstruction: Instruction?

  init(route: Route, config: GuidanceConfig = GuidanceConfig()) {
    self.route = route
    self.config = config
  }

  mutating func reset() {
    along = nil
    arrived = false
    lastInstruction = nil
  }

  mutating func update(_ fix: LocationFix?, now: Date) -> Guidance {
    let progress = (along ?? 0) / max(route.length, 1)
    func blocked(_ why: String) -> Guidance {
      lastInstruction = nil
      return Guidance(
        instruction: arrived ? .arrived : .unavailable(why), relativeBearing: nil,
        distanceRemaining: along.map { route.length - $0 }, crossTrack: nil, offRoute: false,
        progress: arrived ? 1 : progress, target: nil, headingUsed: "none")
    }
    guard let fix else { return blocked("No location yet") }
    if now.timeIntervalSince(fix.timestamp) > config.maxFixAge { return blocked("Location is stale") }
    if fix.horizontalAccuracy < 0 || fix.horizontalAccuracy > config.maxHorizontalAccuracy {
      return blocked("Location too uncertain")
    }

    // Where on the route are we? First fix: anywhere. Later: near the last progress.
    let proj: Route.Projection
    if let along {
      proj = route.project(
        fix.point, minAlong: along - config.backtrackWindow, maxAlong: along + config.forwardWindow)
    } else {
      proj = route.project(fix.point)
    }
    along = proj.along
    let remaining = route.length - proj.along
    let offRoute = proj.crossTrack > max(config.offRouteDistance, fix.horizontalAccuracy)

    if arrived || remaining < config.arriveRadius || Geo.distance(fix.point, route.end) < config.arriveRadius {
      arrived = true
      lastInstruction = .arrived
      return Guidance(
        instruction: .arrived, relativeBearing: nil, distanceRemaining: 0, crossTrack: proj.crossTrack,
        offRoute: false, progress: 1, target: route.end, headingUsed: "none")
    }

    // Aim a little ahead on the path; when far off it, aim back at the path first.
    let target =
      proj.crossTrack > config.lookahead
      ? proj.point
      : route.point(at: proj.along + config.lookahead)
    let bearingToTarget = Geo.bearing(fix.point, target)

    let (facing, used) = pickHeading(fix)
    guard let facing else {
      lastInstruction = nil
      return Guidance(
        instruction: .noHeading, relativeBearing: nil, distanceRemaining: remaining,
        crossTrack: proj.crossTrack, offRoute: offRoute, progress: proj.along / route.length,
        target: target, headingUsed: used)
    }
    let relative = Geo.normalize180(bearingToTarget - facing)
    let instruction = bucket(relative)
    lastInstruction = instruction
    return Guidance(
      instruction: instruction, relativeBearing: relative, distanceRemaining: remaining,
      crossTrack: proj.crossTrack, offRoute: offRoute, progress: proj.along / route.length,
      target: target, headingUsed: used)
  }

  private func pickHeading(_ fix: LocationFix) -> (Double?, String) {
    let compassOK =
      fix.heading != nil && (fix.headingAccuracy ?? .infinity) >= 0
      && (fix.headingAccuracy ?? .infinity) <= config.maxHeadingAccuracy
    let walkingOK = fix.course != nil && (fix.speed ?? 0) >= config.minWalkingSpeed
    switch headingSource {
    case .compass: return compassOK ? (fix.heading, "compass") : (nil, "none")
    case .walking: return walkingOK ? (fix.course, "walking") : (nil, "none")
    case .automatic:
      if compassOK { return (fix.heading, "compass") }
      if walkingOK { return (fix.course, "walking") }
      return (nil, "none")
    }
  }

  /// Angle -> instruction, keeping the previous bucket until the angle is clearly past a boundary.
  private func bucket(_ relative: Double) -> Instruction {
    let raw = Self.rawBucket(relative, config)
    guard let prev = lastInstruction, prev != raw, let p = prev.magnitude, let r = raw.magnitude else { return raw }
    // Pull the angle back toward the previous bucket by the hysteresis margin. If that
    // lands in the previous bucket, the angle is only just over the line: keep it.
    let side = relative < 0 ? -1.0 : 1.0
    let mag = abs(relative)
    let probe = side * max(0, r > p ? mag - config.hysteresisDeg : mag + config.hysteresisDeg)
    return Self.rawBucket(probe, config) == prev ? prev : raw
  }

  static func rawBucket(_ relative: Double, _ c: GuidanceConfig) -> Instruction {
    let a = abs(relative)
    if a < c.aheadDeg { return .ahead }
    if a < c.slightDeg { return relative < 0 ? .slightLeft : .slightRight }
    if a < c.turnDeg { return relative < 0 ? .left : .right }
    return .turnAround
  }
}

// MARK: - Announcements

/// Decides when to speak a route phrase. Pure: feed it the current guidance, whether the
/// hub is currently alerting (obstacle level > 0, fault, or unhealthy), and the time.
///
/// Priority (docs/navigation-research.md §3): fault / obstacle > hold-off > route guidance.
/// While the hub alerts, and for `holdOff` seconds after, it says nothing. It never queues
/// a stale phrase: once quiet time ends it re-reads the current guidance.
struct RouteAnnouncer: Sendable {
  var stableFor = 1.0  // s an instruction must hold before it's spoken
  var repeatEvery = 20.0  // s between reminders of an unchanged instruction
  var holdOff = 1.5  // s of quiet after an obstacle alert or fault ends

  private var candidate: Instruction?
  private var candidateSince: Date?
  private var lastSpoken: Instruction?
  private var lastSpokenAt: Date?
  private var lastAlertAt: Date?
  private var spokeArrival = false

  mutating func reset() { self = RouteAnnouncer(stableFor: stableFor, repeatEvery: repeatEvery, holdOff: holdOff) }

  /// Returns a phrase to speak now, or nil.
  mutating func next(_ g: Guidance, destination: String, hubAlerting: Bool, now: Date) -> String? {
    if hubAlerting {
      lastAlertAt = now
      candidate = nil
      return nil
    }
    if let lastAlertAt, now.timeIntervalSince(lastAlertAt) < holdOff { return nil }

    let instruction = g.instruction
    // Arrival latches in RouteFollower, so it needs no stability wait.
    if instruction == .arrived {
      guard !spokeArrival else { return nil }
      spokeArrival = true
      return remember(instruction, now, Self.phrase(g, destination: destination))
    }

    if instruction != candidate {
      candidate = instruction
      candidateSince = now
    }
    guard let since = candidateSince, now.timeIntervalSince(since) >= stableFor else { return nil }
    let changed = instruction != lastSpoken
    let due = lastSpokenAt.map { now.timeIntervalSince($0) >= repeatEvery } ?? true
    guard changed || due else { return nil }
    return remember(instruction, now, Self.phrase(g, destination: destination))
  }

  private mutating func remember(_ i: Instruction, _ now: Date, _ phrase: String) -> String {
    lastSpoken = i
    lastSpokenAt = now
    return phrase
  }

  /// Every phrase starts with "Route" so it can't be mistaken for an obstacle alert.
  static func phrase(_ g: Guidance, destination: String) -> String {
    let dist = g.distanceRemaining.map { ", \(Self.roundedMetres($0)) metres to \(destination)" } ?? ""
    switch g.instruction {
    case .ahead: return "Route: straight on\(dist)."
    case .slightLeft: return g.offRoute ? "Route: off path, bear left to rejoin." : "Route: bear left\(dist)."
    case .slightRight: return g.offRoute ? "Route: off path, bear right to rejoin." : "Route: bear right\(dist)."
    case .left: return g.offRoute ? "Route: off path, turn left to rejoin." : "Route: turn left\(dist)."
    case .right: return g.offRoute ? "Route: off path, turn right to rejoin." : "Route: turn right\(dist)."
    case .turnAround: return "Route: turn around\(dist)."
    case .arrived: return "Route: near \(destination). Confirm the entrance with your guide."
    case .noHeading: return "Route: hold the phone flat and facing forward, or walk a few steps."
    case .unavailable(let why): return "Route paused: \(why)."
    }
  }

  static func roundedMetres(_ m: Double) -> Int {
    m < 50 ? Int((m / 5).rounded() * 5) : Int((m / 10).rounded() * 10)
  }
}

// MARK: - Hub gate

enum HubGate {
  /// True while the hub is (or might be) telling the wearer something more important than
  /// a route phrase: any obstacle level above zero, a fault, or an unhealthy status.
  /// A missing or stale status is NOT alerting (there is nothing to defer to); the UI
  /// warns separately that obstacle alerts aren't running then.
  static func isAlerting(_ status: HubStatus?, fresh: Bool) -> Bool {
    guard fresh, let status else { return false }
    return status.mode == "fault" || !status.healthy || status.levels.contains { $0 > 0 }
  }
}
