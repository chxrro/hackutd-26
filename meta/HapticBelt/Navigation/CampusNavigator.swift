//
// CampusNavigator.swift
//
// Runs RouteFollower on the phone's GPS + compass (or on SimulatedWalk) at 4 Hz and
// speaks short "Route: ..." phrases. Experimental, for a supervised demo only.
//
// - Speech only. No phone vibration: on this app vibration means obstacles, and a
//   direction-to-walk cue must never feel like an obstacle cue (AGENTS.md).
// - Defers to the hub: while it reports an obstacle, fault or unhealthy state, route
//   speech stops immediately and stays quiet until 1.5 s after it clears.
// - Location stays in memory. Nothing is logged, stored, or sent to the hub.
//

import AVFoundation
import CoreLocation
import Observation
import UIKit

@Observable
@MainActor
final class CampusNavigator: NSObject {
  enum Mode: Equatable { case idle, live, simulated }

  /// Built-in routes, plus "straight test" routes made on the spot.
  private(set) var routes: [Route] = []
  var selectedRoute = 0 {
    didSet { if selectedRoute != oldValue { stop() } }
  }
  var headingSource: HeadingSource = .automatic {
    didSet { follower?.headingSource = headingSource }
  }
  var speechEnabled = true {
    didSet { if !speechEnabled { synth.stopSpeaking(at: .immediate) } }
  }

  private(set) var mode: Mode = .idle
  private(set) var guidance: Guidance?
  private(set) var fix: LocationFix?
  private(set) var authorization: CLAuthorizationStatus
  private(set) var lastPhrase = ""
  private(set) var deferringToHub = false
  private(set) var loadError: String?
  /// Where speech is going right now, e.g. the glasses or "iPhone Speaker".
  private(set) var audioRoute = "No audio output"
  private(set) var audioOnBluetooth = false

  /// Supplied by the screen: is the hub alerting right now?
  @ObservationIgnored var hubAlerting: () -> Bool = { false }
  /// Supplied by the screen: is a fresh, healthy hub status arriving?
  @ObservationIgnored var obstacleAlertsRunning: () -> Bool = { false }

  @ObservationIgnored private let manager = CLLocationManager()
  @ObservationIgnored private let synth = AVSpeechSynthesizer()
  @ObservationIgnored private var follower: RouteFollower?
  @ObservationIgnored private var announcer = RouteAnnouncer()
  @ObservationIgnored private var walker: SimulatedWalk?
  @ObservationIgnored private var loop: Task<Void, Never>?
  @ObservationIgnored private var latestLocation: CLLocation?
  @ObservationIgnored private var latestHeading: HeadingReading?
  @ObservationIgnored private var idleWasDisabled: Bool?
  @ObservationIgnored private var routeObserver: NSObjectProtocol?

  static let tickSeconds = 0.25

  override init() {
    authorization = manager.authorizationStatus
    super.init()
    manager.delegate = self
    manager.desiredAccuracy = kCLLocationAccuracyBest
    manager.activityType = .fitness
    manager.distanceFilter = kCLDistanceFilterNone
    manager.headingFilter = 2
    manager.headingOrientation = .portrait
    loadRoutes()
    synth.usesApplicationAudioSession = true
    refreshAudioRoute()
    routeObserver = NotificationCenter.default.addObserver(
      forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
    ) { [weak self] _ in
      Task { @MainActor in self?.refreshAudioRoute() }
    }
  }

  isolated deinit {
    if let routeObserver { NotificationCenter.default.removeObserver(routeObserver) }
  }

  var route: Route? { routes.indices.contains(selectedRoute) ? routes[selectedRoute] : nil }

  // MARK: Control

  func startLive() {
    guard let route else { return }
    stop()
    switch manager.authorizationStatus {
    case .notDetermined: manager.requestWhenInUseAuthorization()
    case .denied, .restricted:
      say("Location is off for Haptic Belt. Turn it on in Settings.")
      return
    default: break
    }
    begin(route, mode: .live)
    manager.startUpdatingLocation()
    if CLLocationManager.headingAvailable() { manager.startUpdatingHeading() }
  }

  func startSimulated() {
    guard let route else { return }
    stop()
    walker = SimulatedWalk(route: route)
    begin(route, mode: .simulated)
  }

  func stop() {
    loop?.cancel()
    loop = nil
    manager.stopUpdatingLocation()
    manager.stopUpdatingHeading()
    synth.stopSpeaking(at: .immediate)
    walker = nil
    mode = .idle
    deferringToHub = false
    if let idleWasDisabled {
      UIApplication.shared.isIdleTimerDisabled = idleWasDisabled
      self.idleWasDisabled = nil
    }
  }

  /// A 40 m straight route from where you stand, in the direction the phone faces.
  /// For a quick outdoor check of the phone's compass and GPS, anywhere.
  func addStraightTestRoute() {
    guard mode == .idle else { return }
    guard
      let loc = latestLocation, Date().timeIntervalSince(loc.timestamp) < 10, loc.horizontalAccuracy >= 0,
      let heading = latestHeading, Date().timeIntervalSince(heading.timestamp) < 10
    else {
      // Warm up location and compass; the next tap uses them.
      if manager.authorizationStatus == .notDetermined { manager.requestWhenInUseAuthorization() }
      manager.startUpdatingLocation()
      if CLLocationManager.headingAvailable() { manager.startUpdatingHeading() }
      say("Getting location and compass. Face along the test line and tap again in a few seconds.")
      return
    }
    let here = GeoPoint(lat: loc.coordinate.latitude, lon: loc.coordinate.longitude)
    let facing = heading.degrees
    guard
      let r = Route(
        name: "Straight test (40 m)", destination: "the test end point", attribution: "",
        points: [here, Geo.offset(here, meters: 40, bearingDeg: facing)])
    else { return }
    routes.removeAll { $0.name == r.name }
    routes.append(r)
    selectedRoute = routes.count - 1
  }

  // MARK: Loop

  private func begin(_ route: Route, mode: Mode) {
    var f = RouteFollower(route: route)
    f.headingSource = headingSource
    follower = f
    announcer.reset()
    guidance = nil
    fix = nil
    self.mode = mode
    // Keep the screen on while guiding; put back whatever the camera code had set.
    idleWasDisabled = UIApplication.shared.isIdleTimerDisabled
    UIApplication.shared.isIdleTimerDisabled = true
    // Even the opening line waits its turn behind a hub alert or fault.
    if !hubAlerting() {
      let alerts = obstacleAlertsRunning()
        ? "Directions only; obstacles are not checked by directions."
        : "Obstacle alerts are off. Directions only."
      say("Route guidance started to \(route.destination). \(alerts)")
    }
    loop = Task { [weak self] in
      while !Task.isCancelled {
        self?.tick()
        try? await Task.sleep(for: .seconds(Self.tickSeconds))
      }
    }
  }

  private func tick() {
    guard var follower, mode != .idle else { return }
    let now = Date()
    if mode == .simulated, var walker {
      fix = walker.step(dt: Self.tickSeconds, toward: guidance?.target ?? follower.route.points[1], now: now)
      self.walker = walker
    } else {
      fix = liveFix()
    }
    let g = follower.update(fix, now: now)
    self.follower = follower
    guidance = g

    let alerting = hubAlerting()
    deferringToHub = alerting
    let destination = follower.route.destination
    if alerting {
      // Cut route speech mid-word and let the announcer start its quiet period.
      if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
      _ = announcer.next(g, destination: destination, hubAlerting: true, now: now)
    } else if !synth.isSpeaking,
      let phrase = announcer.next(g, destination: destination, hubAlerting: false, now: now)
    {
      say(phrase)
    }
    if g.instruction == .arrived, mode == .live {
      manager.stopUpdatingLocation()
      manager.stopUpdatingHeading()
    }
  }

  private func liveFix() -> LocationFix? {
    guard let loc = latestLocation else { return nil }
    var heading: Double?
    var headingAcc: Double?
    if let h = latestHeading, Date().timeIntervalSince(h.timestamp) < 3 {
      heading = h.degrees
      headingAcc = h.accuracy
    }
    return LocationFix(
      point: GeoPoint(lat: loc.coordinate.latitude, lon: loc.coordinate.longitude),
      horizontalAccuracy: loc.horizontalAccuracy,
      heading: heading, headingAccuracy: headingAcc,
      course: loc.course >= 0 && loc.courseAccuracy >= 0 && loc.courseAccuracy < 45 ? loc.course : nil,
      speed: loc.speed >= 0 ? loc.speed : nil,
      timestamp: loc.timestamp)
  }

  // MARK: Speech

  private func say(_ phrase: String) {
    lastPhrase = phrase
    guard speechEnabled else { return }
    if UIAccessibility.isVoiceOverRunning {
      UIAccessibility.post(notification: .announcement, argument: phrase)
      return
    }
    prepareAudio()
    let u = AVSpeechUtterance(string: phrase)
    u.voice = AVSpeechSynthesisVoice(language: "en-US")
    u.rate = AVSpeechUtteranceDefaultSpeechRate * 1.05
    synth.speak(u)
  }

  /// Same session settings as the hazard-speech output that was heard through the
  /// glasses (playback / spokenAudio / mixWithOthers), so the two never fight over the
  /// route. Re-activating before each phrase recovers from a stale interruption.
  private func prepareAudio() {
    let session = AVAudioSession.sharedInstance()
    if session.category != .playback || session.mode != .spokenAudio
      || session.categoryOptions != [.mixWithOthers]
    {
      try? session.setCategory(.playback, mode: .spokenAudio, options: [.mixWithOthers])
    }
    try? session.setActive(true)
    refreshAudioRoute()
  }

  private func refreshAudioRoute() {
    let outputs = AVAudioSession.sharedInstance().currentRoute.outputs
    let name = outputs.isEmpty ? "No audio output" : outputs.map(\.portName).joined(separator: ", ")
    let bt = outputs.contains { [.bluetoothA2DP, .bluetoothHFP, .bluetoothLE].contains($0.portType) }
    if audioRoute != name { audioRoute = name }
    if audioOnBluetooth != bt { audioOnBluetooth = bt }
  }

  // MARK: Routes

  private func loadRoutes() {
    guard let url = Bundle.main.url(forResource: "ecsw-to-comets-landing", withExtension: "geojson") else {
      loadError = "Route file missing from the app bundle."
      return
    }
    do {
      let r = try Route.parseGeoJSON(Data(contentsOf: url))
      routes = [r, r.reversed(name: "Comets LANding to ECSW", destination: "ECSW")]
    } catch {
      loadError = "Couldn't read the route: \(error)"
    }
  }
}

/// The parts of a CLHeading we use (CLHeading isn't Sendable).
private struct HeadingReading: Sendable {
  let degrees: Double  // true north when the phone knows it, else magnetic
  let accuracy: Double
  let timestamp: Date

  init(_ h: CLHeading) {
    degrees = h.trueHeading >= 0 ? h.trueHeading : h.magneticHeading
    accuracy = h.headingAccuracy
    timestamp = h.timestamp
  }
}

extension CampusNavigator: CLLocationManagerDelegate {
  nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
    guard let last = locations.last else { return }
    MainActor.assumeIsolated { latestLocation = last }
  }

  nonisolated func locationManager(_ manager: CLLocationManager, didUpdateHeading newHeading: CLHeading) {
    let reading = HeadingReading(newHeading)
    MainActor.assumeIsolated { latestHeading = reading }
  }

  nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
    let status = manager.authorizationStatus
    MainActor.assumeIsolated { authorization = status }
  }

  nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
    NSLog("[HapticBelt] location error: \(error.localizedDescription)")
  }
}
