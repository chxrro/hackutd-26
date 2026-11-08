//
// CampusRouteView.swift
//
// Experimental campus route screen: the route on an Apple map, the current spoken
// direction, and whether the hub's obstacle alerts are running. Supervised demo only.
//

import MapKit
import SwiftUI

struct CampusRouteView: View {
  let app: AppModel
  @State private var nav = CampusNavigator()
  @State private var camera: MapCameraPosition = .automatic

  var body: some View {
    // Map pinned above the form: inside a scrolling Form it steals the scroll gesture.
    VStack(spacing: 0) {
      if let route = nav.route {
        RouteMap(route: route, nav: nav, camera: $camera)
          .frame(height: 260)
          .overlay(alignment: .topLeading) {
            Button("Fit route") { camera = Self.fit(route) }
              .font(.footnote.bold())
              .buttonStyle(.borderedProminent)
              .padding(8)
          }
      }
      Form {
        ObstacleAlertBanner(app: app, deferring: nav.deferringToHub)
        if nav.route != nil {
          Section { GuidanceReadout(nav: nav) }
        } else {
          Text(nav.loadError ?? "No route loaded.").foregroundStyle(.red)
        }
        controls
        Section {
          Text(
            "Experimental, supervised demo. Walk with a sighted guide. Directions say where the route goes, not whether it is clear; they never replace a cane, a dog, or the guide. Route speech stops whenever the hub reports an obstacle or a fault."
          )
          Text(
            "Map © Apple. \(nav.route?.attribution ?? ""). Route not yet walked end to end; check doors and crossings on foot."
          )
          .foregroundStyle(.secondary)
        }
        .font(.footnote)
      }
    }
    .navigationTitle("Campus route")
    .navigationBarTitleDisplayMode(.inline)
    .onAppear {
      nav.hubAlerting = { [app] in HubGate.isAlerting(app.hubStatus, fresh: app.isStatusFresh()) }
      if let route = nav.route { camera = Self.fit(route) }
    }
    .onChange(of: nav.selectedRoute) { if let route = nav.route { camera = Self.fit(route) } }
    .onDisappear { nav.stop() }
  }

  /// The whole route with a margin.
  static func fit(_ route: Route) -> MapCameraPosition {
    let rect = route.points
      .map { MKMapRect(origin: MKMapPoint($0.coordinate), size: MKMapSize(width: 0, height: 0)) }
      .reduce(MKMapRect.null) { $0.union($1) }
    let pad = max(rect.width, rect.height) * 0.25 + 50
    return .rect(rect.insetBy(dx: -pad, dy: -pad))
  }

  @ViewBuilder private var controls: some View {
    Section {
      Picker("Route", selection: $nav.selectedRoute) {
        ForEach(Array(nav.routes.enumerated()), id: \.offset) { i, r in
          Text(r.name).tag(i)
        }
      }
      .disabled(nav.mode != .idle)
      Picker("Facing from", selection: $nav.headingSource) {
        Text("Auto").tag(HeadingSource.automatic)
        Text("Compass").tag(HeadingSource.compass)
        Text("Walking").tag(HeadingSource.walking)
      }
      .pickerStyle(.segmented)
      Toggle("Speak directions", isOn: $nav.speechEnabled)
      switch nav.mode {
      case .idle:
        Button("Start live guidance") { nav.startLive() }
        Button("Simulate the walk") { nav.startSimulated() }
        Button("Add 40 m straight test from here") { nav.addStraightTestRoute() }
          .font(.footnote)
      case .live, .simulated:
        Button("Stop guidance", role: .destructive) { nav.stop() }
      }
    } header: {
      Text("Guidance")
    } footer: {
      Text(
        "Compass: hold the phone flat, top pointing where you face. Walking: phone in a pocket or on a lanyard; needs a few steps of steady walking. Simulate: a pretend walker, for testing without going outside."
      )
    }
  }
}

// MARK: - Pieces

private struct ObstacleAlertBanner: View {
  let app: AppModel
  let deferring: Bool

  var body: some View {
    TimelineView(.periodic(from: .now, by: 1)) { context in
      let fresh = app.isStatusFresh(now: context.date)
      let status = fresh ? app.hubStatus : nil
      Section {
        if let status {
          if status.mode == "fault" || !status.healthy {
            Label("Obstacle alerts not reliable: \(status.reason ?? "unknown"). Route speech paused.", systemImage: "exclamationmark.triangle.fill")
              .foregroundStyle(.orange)
          } else if deferring {
            Label("Obstacle alert active. Route speech paused.", systemImage: "hand.raised.fill")
              .foregroundStyle(.red)
          } else {
            Label("Obstacle alerts running (hub connected).", systemImage: "checkmark.circle.fill")
              .foregroundStyle(.green)
          }
        } else {
          Label(
            "Obstacle alerts are NOT running: no status from the hub. Directions only.",
            systemImage: "exclamationmark.octagon.fill"
          )
          .foregroundStyle(.red)
        }
      }
      .accessibilityElement(children: .combine)
    }
  }
}

private struct RouteMap: View {
  let route: Route
  let nav: CampusNavigator
  @Binding var camera: MapCameraPosition

  var body: some View {
    Map(position: $camera) {
      MapPolyline(coordinates: route.points.map(\.coordinate))
        .stroke(.blue, style: StrokeStyle(lineWidth: 5, lineCap: .round, lineJoin: .round))
      Marker("Start", systemImage: "figure.walk", coordinate: route.start.coordinate).tint(.green)
      Marker(route.destination, systemImage: "flag.checkered", coordinate: route.end.coordinate).tint(.red)
      if let target = nav.guidance?.target {
        Annotation("", coordinate: target.coordinate) {
          Circle().stroke(.blue, lineWidth: 2).frame(width: 12, height: 12)
        }
      }
      if nav.mode == .simulated, let p = nav.fix?.point {
        Annotation("Simulated", coordinate: p.coordinate) {
          Image(systemName: "location.north.fill")
            .rotationEffect(.degrees(nav.fix?.heading ?? 0))
            .foregroundStyle(.white)
            .padding(6)
            .background(Circle().fill(.orange))
        }
      } else {
        UserAnnotation()
      }
    }
    .mapControls {
      MapCompass()
      MapScaleView()
      MapUserLocationButton()
    }
    .accessibilityLabel("Map of the route from \(route.name)")
  }
}

private struct GuidanceReadout: View {
  let nav: CampusNavigator

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if nav.mode == .simulated {
        Text("SIMULATED WALK. Not a real position.")
          .font(.caption.bold())
          .foregroundStyle(.orange)
      }
      HStack(spacing: 14) {
        Image(systemName: arrowSymbol)
          .font(.system(size: 44, weight: .semibold))
          .rotationEffect(.degrees(nav.guidance?.relativeBearing ?? 0))
          .foregroundStyle(nav.guidance?.offRoute == true ? .orange : .blue)
          .frame(width: 56)
          .accessibilityHidden(true)
        VStack(alignment: .leading, spacing: 2) {
          Text(headline).font(.title3.bold())
          if let d = nav.guidance?.distanceRemaining, nav.guidance?.instruction != .arrived {
            Text("\(Int(d.rounded())) m to \(nav.route?.destination ?? "destination")")
          }
        }
      }
      ProgressView(value: nav.guidance?.progress ?? 0)
      HStack {
        if let fix = nav.fix { Text("GPS ±\(Int(fix.horizontalAccuracy)) m") } else { Text("GPS –") }
        Spacer()
        Text("facing: \(nav.guidance?.headingUsed ?? "none")")
        Spacer()
        if let x = nav.guidance?.crossTrack { Text("off path \(Int(x)) m") }
      }
      .font(.footnote.monospacedDigit())
      .foregroundStyle(.secondary)
      if !nav.lastPhrase.isEmpty {
        Text("Said: \(nav.lastPhrase)").font(.footnote).foregroundStyle(.secondary)
      }
    }
    .padding(.vertical, 4)
    .accessibilityElement(children: .combine)
  }

  private var headline: String {
    guard nav.mode != .idle else { return "Not guiding" }
    guard let g = nav.guidance else { return "Waiting for location…" }
    switch g.instruction {
    case .ahead: return "Straight on"
    case .slightLeft: return g.offRoute ? "Off path: bear left" : "Bear left"
    case .slightRight: return g.offRoute ? "Off path: bear right" : "Bear right"
    case .left: return g.offRoute ? "Off path: turn left" : "Turn left"
    case .right: return g.offRoute ? "Off path: turn right" : "Turn right"
    case .turnAround: return "Turn around"
    case .arrived: return "Near \(nav.route?.destination ?? "destination")"
    case .noHeading: return "Which way are you facing?"
    case .unavailable(let why): return why
    }
  }

  private var arrowSymbol: String {
    switch nav.guidance?.instruction {
    case .arrived: return "flag.checkered"
    case .noHeading, .unavailable, nil: return "questionmark.circle"
    default: return "arrow.up"
    }
  }
}

extension GeoPoint {
  var coordinate: CLLocationCoordinate2D { CLLocationCoordinate2D(latitude: lat, longitude: lon) }
}
