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
  case starting
  case noCameraFrames
  case fault(String)  // normalized hub reason, so "for 1.2 s" vs "for 1.4 s" is one fault
  case beltTest
  case belt(String)
  case beltMode(String)
  /// Healthy, but input or output is simulated: mock glasses, test pattern, simulated belt.
  case simulated(String)
  case alertsOn

  /// Stable key for de-duplicating announcements.
  var key: String {
    switch self {
    case .fault(let reason): return "fault:\(reason)"
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
    case .stopped, .hubDisconnected, .statusStale, .noCameraFrames, .fault, .beltMode: return true
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
}

enum WearerStatus {
  /// No status for this long means the app can't vouch for the belt (protocol: status at ~5 Hz).
  static let statusTimeout: TimeInterval = 2
  /// No camera frame for this long is a warning (the hub's own timeout is similar).
  static let frameTimeout: TimeInterval = 2

  static func condition(_ input: WearerInputs) -> WearerCondition {
    switch input.session {
    case .off:
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
      // The belt is off either way. A fault outranks "alerts paused" in `reason`,
      // so mention it without making it the headline.
      var detail = "No belt alerts until you resume."
      if !status.healthy, let reason = status.reason, reason != "alerts paused" {
        detail += " The hub also reports: \(shortReason(reason))."
      }
      return WearerCondition(kind: .paused, sentence: "Paused", detail: detail, tone: .warning)
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
    if !status.healthy {
      let reason = shortReason(status.reason)
      return warning(.fault(reason.lowercased()), "Warning: \(reason)", "The hub can’t vouch for alerts right now.")
    }
    switch status.belt {
    case "connected", "simulated":
      break
    case "connecting":
      return warning(.belt("connecting"), "Warning: belt connecting", "No vibration until the belt connects.")
    case "disconnected":
      return warning(.belt("disconnected"), "Warning: belt disconnected", "No vibration until the belt reconnects.")
    default:
      return warning(.belt(status.belt), "Warning: belt \(status.belt)", "The hub reports an unknown belt state.")
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

  /// What VoiceOver should say when the condition changes, or nil for nothing.
  static func announcement(from old: WearerCondition?, to new: WearerCondition) -> String? {
    guard old?.kind != new.kind else { return nil }
    var prefix = ""
    if old?.kind == .paused { prefix = "Resumed. " }
    if case .belt(let state)? = old?.kind, state != "simulated", !new.kind.isFault {
      prefix = "Belt connected. "
    } else if old?.kind.isFault == true, !new.kind.isFault, new.kind != .off {
      prefix = "Recovered. "
    }
    switch new.kind {
    case .off:
      return "Alerts off."
    case .diagnostic:
      return "Diagnostic mode. No belt alerts."
    case .stopped:
      return "Warning: alerts stopped. \(new.detail)"
    case .paused:
      return "Paused. No belt alerts."
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
