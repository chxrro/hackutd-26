//
// WearerStatus.swift
//
// Turns what the app knows (session, hub link, latest hub status, camera
// frames) into the one short sentence the wearer hears and sees.
//
// Rules (AGENTS.md, docs/protocol.md):
//   - Anything the app can't vouch for is a warning, never calm: a stale or
//     missing status, a dropped link, healthy:false, paused, a belt test, a
//     belt that isn't connected, simulated input or output.
//   - Wording describes the system, never where to walk, and never claims a
//     clear path. "Alerts on" means alerts are working, not that nothing is there.
//   - Paused is not silent (protocol "Belt cues"): the hub plays a pause cue and
//     a soft reminder pulse. A real fault while paused outranks the pause, as it
//     does on the hub, and is the headline: "Warning: no camera frames (alerts paused)".
//   - The short "resuming alerts" cue after Resume is not a warning. It is only
//     announced as a warning if it lasts longer than `resumeTimeout`.
//   - Stop tells the hub (`end_session`), so the belt plays the session-ended cue
//     and stays quiet: "Stopped. The belt will not alert." If the hub wasn't told
//     (no control code, no link, refused, no reply), it can't tell the stop from
//     a lost phone and plays the fault pattern, and the app says so.
//
// Pure logic, no UIKit, so it is unit tested.
//

import Foundation

enum WearerTone: Equatable, Sendable {
  case off  // not running: neutral, but not reassuring
  case ok  // the hub is vouching for its alerts
  case warning  // can't vouch: never shown as calm
}

enum WearerKind: Hashable, Sendable {
  case off
  case diagnostic
  case stopped
  case hubDisconnected
  case statusStale
  case paused
  /// A real fault while alerts are paused (normalized reason). Faults outrank pause.
  case pausedFault(String)
  /// The hub's short resume cue ("resuming alerts"): neither calm nor a warning, and not announced.
  case resuming
  /// "resuming alerts" for longer than `WearerStatus.resumeTimeout`: unexpected, so a warning.
  case resumeStuck
  /// The wearer pressed Stop; waiting for the hub to confirm `end_session`. Not announced.
  case stopping
  /// The hub confirmed the stop: session-ended cue, then a quiet belt.
  case stoppedByWearer
  /// The wearer pressed Stop but the hub wasn't told: it plays the fault pattern.
  case stopNotConfirmed
  /// The hub confirmed the stop, but its status now says it isn't stopped (e.g. it restarted).
  case stopUndone
  case starting
  case noCameraFrames
  case fault(String)  // normalized hub reason, so "for 1.2 s" vs "for 1.4 s" is one fault
  case beltTest
  /// The belt's BLE link is down or coming back: `disconnected` and `connecting` are one
  /// kind, whether the hub says so in `reason` or only in `belt`, so a reconnect cycle
  /// (disconnected -> connecting -> connected) is announced once, not per step.
  case beltLink
  /// Link up, but the belt stopped acknowledging commands (`"belt not acknowledging"`).
  case beltNotResponding
  /// Any other belt state the app doesn't know.
  case belt(String)
  case beltMode(String)
  /// Healthy, but input or output is simulated: mock glasses, test pattern, simulated belt.
  case simulated(String)
  case alertsOn

  /// Shown, never spoken: short states that resolve by themselves.
  var isQuiet: Bool { self == .resuming || self == .stopping }

  /// Stable key for de-duplicating announcements.
  var key: String {
    switch self {
    case .fault(let reason): return "fault:\(reason)"
    case .pausedFault(let reason): return "pausedFault:\(reason)"
    case .belt(let state): return "belt:\(state)"
    case .beltMode(let mode): return "mode:\(mode)"
    case .simulated(let parts): return "simulated:\(parts)"
    default: return String(describing: self)
    }
  }

  /// A real problem (as opposed to off, paused, starting or a known simulation):
  /// leaving one of these is announced as "Recovered".
  var isFault: Bool {
    switch self {
    case .stopped, .hubDisconnected, .statusStale, .noCameraFrames, .fault, .beltMode, .pausedFault, .resumeStuck,
      .beltLink, .beltNotResponding:
      return true
    case .belt(let state): return state != "simulated"
    default: return false
    }
  }
}

struct WearerCondition: Equatable, Sendable {
  var kind: WearerKind
  /// Large, short: "Alerts on", "Paused", "Warning: no camera frames".
  var sentence: String
  /// One plain-language line under it.
  var detail: String
  var tone: WearerTone
}

/// The wearer pressed Stop in this app. Cleared by Start.
enum WearerStop: Equatable, Sendable {
  /// `end_session` sent; no reply yet.
  case sending
  /// The hub acknowledged `end_session`.
  case confirmed
  /// Not sent or refused (no control code, hub not connected, not paired, no reply, older hub).
  case notConfirmed(String)
}

struct WearerInputs: Equatable, Sendable {
  enum Session: Equatable, Sendable {
    case off
    /// The source stopped by itself (glasses disconnected, no frames, ...).
    case stopped(reason: String)
    /// The Advanced viewer or on-screen preview is using the camera; no alerts.
    case diagnostic
    /// Started; no camera frame yet.
    case starting
    case running
  }

  var session: Session = .off
  var source: FrameSource = .glasses
  /// Glasses frames come from Debug mock glasses (MockDeviceKit), not the Ray-Bans.
  var mockGlasses = false
  var paired = false
  var link: HubClient.State = .disconnected
  var status: HubStatus?
  /// Seconds since the last hub status on this connection; nil if none yet.
  var statusAge: TimeInterval?
  /// Seconds since the last frame from the source; nil before the first.
  var cameraFrameAge: TimeInterval?
  /// Seconds the hub has reported `reason: "resuming alerts"` without a break; nil if it isn't.
  var resumingFor: TimeInterval?
  var wearerStop: WearerStop?
  /// The hub's own word on the stop: `stopped` from a status received after the
  /// request (if true) or after the hub acknowledged it (if false); nil if none.
  var hubStopped: Bool?
}

enum WearerStatus {
  /// No status for this long means the app can't vouch for the belt (protocol: status at ~5 Hz).
  static let statusTimeout: TimeInterval = 2
  /// No camera frame for this long is a warning (the hub's own timeout is similar).
  static let frameTimeout: TimeInterval = 2
  /// The hub's resume cue lasts about 0.6 s. Still "resuming alerts" after this is a warning.
  static let resumeTimeout: TimeInterval = 2
  /// Hub status reasons that describe a deliberate state, not a fault.
  static let pausedReason = "alerts paused"
  static let resumingReason = "resuming alerts"
  /// Belt reasons (protocol section 2, hub PR #23). Older hubs report a down link
  /// only in `belt`, with `healthy: true`; newer ones also set `healthy: false`.
  static let beltDisconnectedReason = "belt disconnected"
  static let beltConnectingReason = "belt connecting"
  static let beltNotAcknowledgingReason = "belt not acknowledging"

  static let pausedSentence = "Paused"
  static let pausedDetail =
    "Alerts are off; the belt pulses softly every 15 seconds as a reminder. Silence does not mean the path is clear."

  static func condition(_ input: WearerInputs) -> WearerCondition {
    switch input.session {
    case .off:
      if let stop = input.wearerStop { return stopped(stop, input) }
      return WearerCondition(
        kind: .off, sentence: "Alerts off",
        detail: input.paired
          ? "Press Start to turn on belt alerts."
          : "The hub isn’t set up yet. A helper can pair it in Setup.",
        tone: .off)
    case .diagnostic:
      return WearerCondition(
        kind: .diagnostic, sentence: "Warning: diagnostic mode",
        detail: "The camera is in use by an Advanced diagnostic. No belt alerts.", tone: .warning)
    case .stopped(let reason):
      return WearerCondition(
        kind: .stopped, sentence: "Warning: alerts stopped", detail: reason, tone: .warning)
    case .starting, .running:
      break
    }

    guard input.link == .connected else {
      var detail = "No belt alerts from this phone until it reconnects."
      if case .failed(let why) = input.link { detail = why }
      return warning(.hubDisconnected, "Warning: hub not connected", detail)
    }
    guard let status = input.status, let age = input.statusAge, age <= statusTimeout else {
      return warning(.statusStale, "Warning: no status from hub", "The hub stopped reporting. Alerts can’t be trusted.")
    }
    if status.isPaused {
      // Same priority as the hub: a real fault outranks the pause and is the headline.
      if let fault = pausedFault(input, status) {
        return warning(
          .pausedFault(fault.key), "Warning: \(fault.text) (alerts paused)",
          "Alerts are paused, and the hub reports a problem. The hub can’t vouch for the belt right now.")
      }
      return WearerCondition(kind: .paused, sentence: pausedSentence, detail: pausedDetail, tone: .warning)
    }
    if input.session == .starting {
      return warning(.starting, "Starting", "Waiting for camera frames. No alerts yet.")
    }
    if let frameAge = input.cameraFrameAge, frameAge > frameTimeout {
      return warning(.noCameraFrames, "Warning: no camera frames", "The phone isn’t getting frames from the camera.")
    }
    if status.isBeltTest {
      return warning(.beltTest, "Belt test", "These vibrations are a test, not obstacles.")
    }
    if !status.healthy, status.reason == resumingReason {
      if let resumingFor = input.resumingFor, resumingFor > resumeTimeout {
        return warning(
          .resumeStuck, "Warning: alerts not resumed",
          "The hub has been resuming for over 2 seconds. Alerts can’t be trusted.")
      }
      // The ~0.6 s resume cue: those vibrations are the cue, not obstacles.
      return warning(.resuming, "Resuming alerts", "The belt is playing the resume cue. These vibrations are not obstacles.")
    }
    // Hub precedence: camera/perception faults outrank belt reasons in `reason`, so a
    // non-belt reason is the headline even if `belt` also says disconnected.
    if !status.healthy, let reason = status.reason, !isBeltReason(reason) {
      let short = shortReason(reason)
      return warning(.fault(short.lowercased()), "Warning: \(short)", "The hub can’t vouch for alerts right now.")
    }
    if let belt = beltProblem(status) { return belt }
    if !status.healthy {
      let short = shortReason(status.reason)
      return warning(.fault(short.lowercased()), "Warning: \(short)", "The hub can’t vouch for alerts right now.")
    }
    switch status.mode {
    case "levels":
      break
    case "fault":
      return warning(.beltMode("fault"), "Warning: belt fault pattern", "The hub is sending the fault pattern.")
    default:
      return warning(.beltMode(status.mode), "Warning: hub alerts off", "The hub isn’t sending belt alerts.")
    }
    // Healthy, but is any of it simulated? Name every simulated part.
    var parts: [(key: String, sentence: String, detail: String)] = []
    if input.source == .test {
      parts.append(("test", "test pattern only", "Test pattern: frames are simulated, not the glasses camera."))
    } else if input.mockGlasses {
      parts.append(("mock", "mock glasses", "Mock glasses: frames are simulated, not from the Ray-Bans."))
    }
    if status.belt == "simulated" {
      parts.append(("belt", "simulated belt", "Simulated belt: the hub isn’t driving a real belt. No vibration."))
    }
    if !parts.isEmpty {
      let sentence = parts.count == 1 ? "Warning: \(parts[0].sentence)" : "Warning: simulation only"
      return warning(
        .simulated(parts.map(\.key).joined(separator: "+")), sentence,
        parts.map(\.detail).joined(separator: " "))
    }
    return WearerCondition(
      kind: .alertsOn, sentence: "Alerts on",
      detail: "The belt vibrates toward obstacles it detects. Silence does not mean the path is clear.",
      tone: .ok)
  }

  static let stoppedSentence = "Stopped"
  static let stoppedDetail =
    "The belt will not alert. Silence does not mean the path is clear. Press Start to start alerts again."

  /// After Stop. A fresh hub status is the hub's own word and wins over the reply.
  private static func stopped(_ stop: WearerStop, _ input: WearerInputs) -> WearerCondition {
    let fresh = input.link == .connected && (input.statusAge.map { $0 <= statusTimeout } ?? false)
    if input.hubStopped == true {
      return WearerCondition(kind: .stoppedByWearer, sentence: stoppedSentence, detail: stoppedDetail, tone: .off)
    }
    switch stop {
    case .sending:
      return WearerCondition(kind: .stopping, sentence: "Stopping", detail: "Telling the hub to stop the belt.", tone: .off)
    case .confirmed:
      if fresh, input.hubStopped == false {
        return warning(
          .stopUndone, "Warning: hub no longer stopped",
          "The hub isn’t in the stopped state any more, so the belt may play its fault pattern. Press Start, or stop the hub.")
      }
      return WearerCondition(kind: .stoppedByWearer, sentence: stoppedSentence, detail: stoppedDetail, tone: .off)
    case .notConfirmed(let why):
      return warning(
        .stopNotConfirmed, "Stopped. Hub not told",
        "\(why) The hub can’t tell this from a lost phone, so the belt plays its fault pattern. Press Start, or stop the hub.")
    }
  }

  private static func isBeltReason(_ reason: String) -> Bool {
    [beltDisconnectedReason, beltConnectingReason, beltNotAcknowledgingReason].contains(reason)
  }

  /// A belt problem from `reason` (newer hubs) or `belt` (any hub), or nil.
  /// Link down outranks not acknowledging, as on the hub.
  private static func beltProblem(_ status: HubStatus) -> WearerCondition? {
    let reason = status.healthy ? nil : status.reason
    if status.belt == "disconnected" || reason == beltDisconnectedReason {
      return warning(
        .beltLink, "Warning: belt disconnected",
        "The belt plays its fault pattern, then goes quiet. No obstacle alerts until it reconnects.")
    }
    if status.belt == "connecting" || reason == beltConnectingReason {
      return warning(.beltLink, "Warning: belt connecting", "No obstacle alerts until the belt connects.")
    }
    if reason == beltNotAcknowledgingReason {
      return warning(
        .beltNotResponding, "Warning: belt not responding",
        "The belt isn’t confirming commands. It may be playing its fault pattern, or nothing. The hub is reconnecting.")
    }
    switch status.belt {
    case "connected", "simulated": return nil
    default:
      return warning(.belt(status.belt), "Warning: belt \(status.belt)", "The hub reports an unknown belt state.")
    }
  }

  /// A real fault to headline while paused (key for de-duplication, text for the
  /// sentence), or nil if it is just paused. "alerts paused" (and a missing reason)
  /// is the pause itself, not a fault.
  private static func pausedFault(_ input: WearerInputs, _ status: HubStatus) -> (key: String, text: String)? {
    if input.session == .running, let frameAge = input.cameraFrameAge, frameAge > frameTimeout {
      return ("no camera frames", "no camera frames")
    }
    if !status.healthy, let reason = status.reason,
      ![pausedReason, resumingReason, "belt test"].contains(reason), !isBeltReason(reason)
    {
      let short = shortReason(reason)
      return (short.lowercased(), short)
    }
    if let belt = beltProblem(status) {
      let text = belt.sentence.replacingOccurrences(of: "Warning: ", with: "")
      return (belt.kind.key, text)
    }
    if status.mode == "fault" { return ("belt fault pattern", "belt fault pattern") }
    return nil
  }

  /// What VoiceOver should say when the condition changes, or nil for nothing.
  /// The short resume cue (`.resuming`) is never announced: the caller should
  /// leave its record of what the wearer heard alone, so the state after it is
  /// worded against "Paused" ("Resumed. Alerts on.").
  static func announcement(from old: WearerCondition?, to new: WearerCondition) -> String? {
    guard old?.kind != new.kind, !new.kind.isQuiet else { return nil }
    let wasPaused: Bool
    switch old?.kind {
    case .paused?, .pausedFault?, .resuming?: wasPaused = true
    default: wasPaused = false
    }
    // Only kinds read from a fresh status that says `paused: false` can confirm a resume.
    // Hub not connected, stale status, stopped or a stuck resume can't.
    let confirmsResume: Bool
    switch new.kind {
    case .alertsOn, .simulated, .fault, .belt, .beltLink, .beltNotResponding, .beltMode, .beltTest, .noCameraFrames,
      .starting:
      confirmsResume = true
    default: confirmsResume = false
    }
    var prefix = ""
    if wasPaused, confirmsResume { prefix = "Resumed. " }
    if old?.kind == .beltLink, !new.kind.isFault {
      prefix = "Belt connected. "
    } else if old?.kind.isFault == true, !new.kind.isFault, new.kind != .off {
      prefix = "Recovered. "
    }
    switch new.kind {
    case .off:
      return "Alerts off."
    case .stoppedByWearer:
      return "Stopped. The belt will not alert."
    case .stopNotConfirmed:
      return "Stopped. The hub wasn’t told, so the belt plays its fault pattern."
    case .stopUndone:
      return "Warning: the hub is no longer stopped. The belt may play its fault pattern."
    case .diagnostic:
      return "Diagnostic mode. No belt alerts."
    case .stopped:
      return "Warning: alerts stopped. \(new.detail)"
    case .paused:
      // After a fault cleared while still paused, the hub replays the pause cue.
      if prefix == "Recovered. " { return "Recovered. Still paused. Alerts are off." }
      return "Paused. Alerts are off; the belt pulses softly every 15 seconds as a reminder."
    case .pausedFault:
      return sentence(new.sentence.replacingOccurrences(of: " (alerts paused)", with: "")) + ". Alerts paused."
    case .starting:
      return "Starting. No alerts yet."
    case .beltTest:
      return "Belt test. These vibrations are not obstacles."
    case .alertsOn:
      return prefix + "Alerts on."
    case .simulated(let parts):
      let names = parts.split(separator: "+").map { part -> String in
        switch part {
        case "test": return "test pattern"
        case "mock": return "mock glasses"
        default: return "simulated belt"
        }
      }
      let list = names.count > 1 ? ": " + names.joined(separator: " and ") : ""
      return prefix + sentence(new.sentence) + list + "."
    default:
      return prefix + sentence(new.sentence) + "."
    }
  }

  /// "no camera frames for 1.2 s" -> "no camera frames". Keeps the sentence
  /// short and stable, so a reason whose number changes every status isn't
  /// announced as a new fault.
  static func shortReason(_ reason: String?) -> String {
    let raw = (reason ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    let pattern = #"\s*(for|after|in|over)?\s*\d+(\.\d+)?\s*(ms|s|sec|secs|seconds?)\b"#
    var text = raw.replacingOccurrences(of: pattern, with: "", options: [.regularExpression, .caseInsensitive])
    text = text.replacingOccurrences(of: #"\s*\(\s*\)"#, with: "", options: .regularExpression)
    text = text.trimmingCharacters(in: CharacterSet(charactersIn: " .,:;"))
    return text.isEmpty ? "hub can’t vouch for alerts" : text
  }

  private static func warning(_ kind: WearerKind, _ sentence: String, _ detail: String) -> WearerCondition {
    WearerCondition(kind: kind, sentence: sentence, detail: detail, tone: .warning)
  }

  private static func sentence(_ text: String) -> String {
    text.hasSuffix(".") ? String(text.dropLast()) : text
  }
}
