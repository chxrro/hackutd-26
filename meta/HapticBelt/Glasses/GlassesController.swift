//
// GlassesController.swift
//
// Everything that talks to the Meta Wearables SDK lives in this folder. The rest
// of the app sees plain Swift state and a frame callback, never SDK types.
//
// Flow (same as Meta's CameraAccess sample, collapsed into one Start button):
//   register with Meta AI -> start DeviceSession -> wait for .started
//   -> camera permission (may bounce to Meta AI) -> addCamera -> stream.start()
//

import MWDATCamera
import MWDATCore
import Observation
import UIKit

@Observable
@MainActor
final class GlassesController {
  // MARK: State for the UI

  private(set) var isRegistered = false
  private(set) var isRegistering = false
  private(set) var hasActiveDevice = false
  private(set) var sessionStateText = "idle"
  private(set) var streamStateText = "stopped"
  private(set) var isStreaming = false
  /// The user pressed Start and hasn't pressed Stop.
  private(set) var wantsStream = false
  var errorMessage: String?

  var canStart: Bool { isRegistered && !wantsStream }

  /// Called on the main actor with the stream state name (docs/protocol.md names).
  @ObservationIgnored var onStreamStateChange: (@MainActor (String) -> Void)?

  // MARK: Private

  @ObservationIgnored private let wearables: WearablesInterface
  @ObservationIgnored private let deviceSelector: AutoDeviceSelector
  @ObservationIgnored private let streamConfig: StreamConfiguration
  @ObservationIgnored private let onFrame: @Sendable (UIImage) -> Void
  @ObservationIgnored private var session: DeviceSession?
  @ObservationIgnored private var camera: MWDATCamera.Camera?
  @ObservationIgnored private let sessionTokens = ListenerTokenBag()
  @ObservationIgnored private let streamTokens = ListenerTokenBag()
  @ObservationIgnored private var monitorTasks: [Task<Void, Never>] = []

  /// - Parameter onFrame: called on an SDK thread for every decoded frame. Must be cheap.
  init(wearables: WearablesInterface, onFrame: @escaping @Sendable (UIImage) -> Void) {
    self.wearables = wearables
    self.deviceSelector = AutoDeviceSelector(wearables: wearables)
    // Medium (504x896) at 15 fps: plenty for obstacle detection, and lower
    // settings get less Bluetooth compression per frame.
    self.streamConfig = StreamConfiguration(videoCodec: .raw, resolution: .medium, frameRate: 15)
    self.onFrame = onFrame
    applyRegistration(wearables.registrationState)
    startMonitoring()
  }

  isolated deinit {
    monitorTasks.forEach { $0.cancel() }
    session?.stop()
  }

  // MARK: Registration

  func register() {
    guard !isRegistering else { return }
    Task {
      do {
        try await wearables.startRegistration()
      } catch let error as RegistrationError {
        errorMessage = error.description
      } catch {
        errorMessage = error.localizedDescription
      }
    }
  }

  func unregister() {
    stop()
    Task {
      do {
        try await wearables.startUnregistration()
      } catch {
        errorMessage = error.localizedDescription
      }
    }
  }

  /// Meta AI returns to the app through the URL scheme after registration and permission prompts.
  func handleOpenURL(_ url: URL) {
    guard
      let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
      components.queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true
    else { return }
    Task {
      do {
        _ = try await Wearables.shared.handleUrl(url)
      } catch {
        errorMessage = error.localizedDescription
      }
    }
  }

  // MARK: Streaming

  func start() {
    guard isRegistered, !wantsStream else { return }
    wantsStream = true
    do throws(DeviceSessionError) {
      let session = try wearables.createSession(deviceSelector: deviceSelector)
      self.session = session
      observe(session)  // subscribe before start() so no transition is missed
      try session.start()
    } catch {
      errorMessage = error.localizedDescription
      teardownSession()
    }
  }

  func stop() {
    wantsStream = false
    if let session {
      session.stop()  // cascades to the camera and its stream
    } else {
      teardownSession()
    }
  }

  private func startCameraIfReady() async {
    guard wantsStream, camera == nil, let session, session.state == .started else { return }
    do {
      if try await wearables.checkPermissionStatus(.camera) != .granted {
        // Switches to the Meta AI app and comes back through handleOpenURL.
        guard try await wearables.requestPermission(.camera) == .granted else {
          errorMessage = "Camera permission was denied in Meta AI."
          stop()
          return
        }
      }
      guard wantsStream, camera == nil, session.state == .started else { return }
      guard let camera = try session.addCamera(config: streamConfig) else {
        errorMessage = "Couldn't attach the glasses camera. Try again."
        return
      }
      self.camera = camera
      observe(camera.stream)
      camera.stream.start()
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  // MARK: SDK observation

  private func startMonitoring() {
    let wearables = self.wearables
    let selector = self.deviceSelector
    monitorTasks.append(
      Task { [weak self] in
        for await state in wearables.registrationStateStream() {
          self?.applyRegistration(state)
        }
      })
    monitorTasks.append(
      Task { [weak self] in
        for await deviceId in selector.activeDeviceStream() {
          self?.hasActiveDevice = deviceId != nil
        }
      })
  }

  private func applyRegistration(_ state: RegistrationState) {
    isRegistered = state == .registered
    isRegistering = state == .registering
  }

  private func observe(_ session: DeviceSession) {
    session.statePublisher.listen { [weak self] state in
      Task { @MainActor in self?.sessionChanged(state) }
    }.store(in: sessionTokens)
    session.errorPublisher.listen { [weak self] error in
      Task { @MainActor in self?.errorMessage = error.localizedDescription }
    }.store(in: sessionTokens)
  }

  private func observe(_ stream: MWDATCamera.Stream) {
    stream.statePublisher.listen { [weak self] state in
      Task { @MainActor in self?.streamChanged(state) }
    }.store(in: streamTokens)
    stream.errorPublisher.listen { [weak self] error in
      Task { @MainActor in self?.errorMessage = error.localizedDescription }
    }.store(in: streamTokens)
    let onFrame = self.onFrame
    stream.videoFramePublisher.listen { frame in
      // SDK thread. makeUIImage() decodes the .raw frame; hand it straight on.
      if let image = frame.makeUIImage() { onFrame(image) }
    }.store(in: streamTokens)
  }

  private func sessionChanged(_ state: DeviceSessionState) {
    sessionStateText = state.description
    switch state {
    case .started:
      Task { await startCameraIfReady() }
    case .stopped:
      teardownSession()
    default:
      break
    }
  }

  private func streamChanged(_ state: StreamState) {
    let name = Self.name(of: state)
    streamStateText = name
    isStreaming = state == .streaming
    onStreamStateChange?(name)
    if state == .stopped {
      streamTokens.clear()
      camera?.stop()
      camera = nil
    }
  }

  private func teardownSession() {
    streamTokens.clear()
    sessionTokens.clear()
    camera = nil
    session = nil
    isStreaming = false
    wantsStream = false
    sessionStateText = "idle"
    streamStateText = "stopped"
    onStreamStateChange?("stopped")
  }

  /// Names match docs/protocol.md ("phone_status" message).
  private static func name(of state: StreamState) -> String {
    switch state {
    case .stopped: return "stopped"
    case .waitingForDevice: return "waitingForDevice"
    case .starting: return "starting"
    case .streaming: return "streaming"
    case .paused: return "paused"
    case .stopping: return "stopping"
    }
  }
}
