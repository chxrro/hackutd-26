import AVFAudio
import Observation

/// Uses the phone's selected Bluetooth audio route. Never silently speaks from
/// the phone speaker. A route name is evidence of routing, not proof of audibility.
@Observable
@MainActor
final class SpeechOutput: NSObject, AVSpeechSynthesizerDelegate {
  private(set) var routeName = "No Bluetooth output selected"
  private(set) var bluetoothReady = false
  private(set) var interrupted = false
  private var routeRequested = false
  @ObservationIgnored private var routeID = ""
  private(set) var setupError: String?
  private(set) var volume: Float = 0
  var error: String? {
    if let setupError { return setupError }
    if routeRequested && !bluetoothReady {
      return "Glasses may be connected without being selected for audio. Use Choose audio output."
    }
    return nil
  }
  private(set) var lastPhrase = "No speech requested"
  private(set) var playbackStatus = "No speech requested"
  @ObservationIgnored private var currentUtterance: ObjectIdentifier?
  @ObservationIgnored private var startTimeout: Task<Void, Never>?
  @ObservationIgnored private let synthesizer = AVSpeechSynthesizer()
  @ObservationIgnored private var observers: [NSObjectProtocol] = []

  override init() {
    super.init()
    synthesizer.usesApplicationAudioSession = true
    synthesizer.delegate = self
    refreshRoute()
    observers.append(NotificationCenter.default.addObserver(
      forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main
    ) { [weak self] _ in
      Task { @MainActor in
        guard let self else { return }
        self.refreshRoute()
      }
    })
    observers.append(NotificationCenter.default.addObserver(
      forName: AVAudioSession.interruptionNotification, object: nil, queue: .main
    ) { [weak self] notification in
      let began = (notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
        == AVAudioSession.InterruptionType.began.rawValue
      Task { @MainActor in
        self?.interrupted = began
        self?.stop(reason: began ? "Interrupted by iOS" : "Interruption ended; ready for a new request")
        self?.refreshRoute()
      }
    })
  }

  isolated deinit {
    startTimeout?.cancel()
    observers.forEach { NotificationCenter.default.removeObserver($0) }
  }

  func refreshRoute() {
    let session = AVAudioSession.sharedInstance()
    let outputs = session.currentRoute.outputs
    if volume != session.outputVolume { volume = session.outputVolume }
    let nextID = outputs.map(\.uid).joined(separator: "|")
    if !routeID.isEmpty && nextID != routeID { stop(reason: "Stopped: audio output changed") }
    routeID = nextID
    let ready = outputs.contains { [.bluetoothA2DP, .bluetoothHFP, .bluetoothLE].contains($0.portType) }
    let name = outputs.isEmpty ? "No audio output" : outputs.map(\.portName).joined(separator: ", ")
    if bluetoothReady != ready { bluetoothReady = ready }
    if routeName != name { routeName = name }
  }

  @discardableResult
  func prepare(requestRoute: Bool = true) -> Bool {
    routeRequested = routeRequested || requestRoute
    do {
      let session = AVAudioSession.sharedInstance()
      // Avoid forcing a new route change for every utterance.
      if session.category != .playback || session.mode != .spokenAudio
          || session.categoryOptions != [.mixWithOthers] {
        try session.setCategory(.playback, mode: .spokenAudio, options: [.mixWithOthers])
      }
      try session.setActive(true)
      // An interruption-ended notification is not guaranteed. Successful explicit
      // activation is the current evidence that the session is available again.
      interrupted = false
      refreshRoute()
      setupError = nil
      return bluetoothReady && !interrupted
    } catch {
      self.setupError = "Speech unavailable: \(error.localizedDescription)"
      return false
    }
  }

  func speak(_ text: String, active: Bool = true) {
    stop()
    lastPhrase = text
    guard active else {
      playbackStatus = "Blocked: app is inactive"
      return
    }
    guard prepare() else {
      playbackStatus = error ?? "Blocked: audio session unavailable"
      return
    }
    let utterance = AVSpeechUtterance(string: text)
    utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
    utterance.rate = AVSpeechUtteranceDefaultSpeechRate
    let id = ObjectIdentifier(utterance)
    currentUtterance = id
    playbackStatus = "Waiting for iOS speech to start"
    synthesizer.speak(utterance)
    startTimeout = Task { [weak self] in
      do { try await Task.sleep(for: .seconds(5)) } catch { return }
      guard let self, self.currentUtterance == id else { return }
      self.stop(reason: "iOS did not start speech; tap Test glasses speech to retry")
    }
  }

  func stop(reason: String = "Speech stopped") {
    startTimeout?.cancel()
    startTimeout = nil
    if currentUtterance != nil { playbackStatus = reason }
    currentUtterance = nil
    synthesizer.stopSpeaking(at: .immediate)
  }

  nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                    didStart utterance: AVSpeechUtterance) {
    let id = ObjectIdentifier(utterance)
    Task { @MainActor [weak self] in
      guard let self, self.currentUtterance == id else { return }
      self.startTimeout?.cancel()
      self.startTimeout = nil
      self.playbackStatus = "Speaking through \(self.routeName)"
    }
  }

  nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                    didFinish utterance: AVSpeechUtterance) {
    let id = ObjectIdentifier(utterance)
    Task { @MainActor [weak self] in
      self?.completed(id, status: "iOS finished speech — confirm you heard it")
    }
  }

  nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                    didCancel utterance: AVSpeechUtterance) {
    let id = ObjectIdentifier(utterance)
    Task { @MainActor [weak self] in
      self?.completed(id, status: "Speech canceled by iOS")
    }
  }

  private func completed(_ id: ObjectIdentifier, status: String) {
    guard currentUtterance == id else { return }
    startTimeout?.cancel()
    startTimeout = nil
    currentUtterance = nil
    playbackStatus = status
  }
}
