//
// Announcements.swift
//
// Keeps VoiceOver from chattering. State announcements are de-duplicated by
// key and rate limited; when one is held back, only the newest is kept and it
// is spoken as soon as the window opens, unless the state has already gone
// back to what was last spoken. Obstacle-kind speech ("Person ahead") is
// optional, off by default, and has its own slower limits.
//
// Pure logic with an injected clock (seconds), so it is unit tested.
//

import Foundation

struct Announcement: Equatable, Sendable {
  var key: String
  var text: String
}

/// De-duplication and rate limiting for state announcements.
struct AnnouncementGate: Sendable {
  let minInterval: TimeInterval
  private(set) var lastKey: String?
  private(set) var lastAt: TimeInterval?
  private(set) var pending: Announcement?

  /// `initialKey` is the state on screen at launch, which VoiceOver reads anyway.
  init(minInterval: TimeInterval = 3, initialKey: String? = nil) {
    self.minInterval = minInterval
    self.lastKey = initialKey
  }

  /// Offer a new announcement. Returns what to speak now, if anything.
  mutating func submit(_ announcement: Announcement, now: TimeInterval) -> Announcement? {
    if announcement.key == lastKey {
      pending = nil  // flapped back to what the wearer last heard
      return nil
    }
    if let lastAt, now - lastAt < minInterval {
      pending = announcement  // newest wins
      return nil
    }
    return speak(announcement, now)
  }

  /// The state went back to what the wearer last heard: say nothing about it.
  mutating func dropPending() {
    pending = nil
  }

  /// Call periodically. Returns a held-back announcement once its window opens.
  mutating func poll(now: TimeInterval) -> Announcement? {
    guard let pending else { return nil }
    if let lastAt, now - lastAt < minInterval { return nil }
    return speak(pending, now)
  }

  private mutating func speak(_ announcement: Announcement, _ now: TimeInterval) -> Announcement {
    pending = nil
    lastKey = announcement.key
    lastAt = now
    return announcement
  }
}

/// "Person ahead", "Object left": the kind and direction of the strongest
/// current alert. Describes where the obstacle is, never where to walk.
enum SectorSpeech {
  /// Direction names for the belt's motors, index 0 = wearer's far left.
  static func direction(index: Int, count: Int) -> String? {
    guard count > 0, (0..<count).contains(index) else { return nil }
    if count == 5 { return ["far left", "left", "ahead", "right", "far right"][index] }
    if count == 1 { return "ahead" }
    let center = (Double(index) + 0.5) / Double(count)
    return center < 1.0 / 3 ? "left" : center > 2.0 / 3 ? "right" : "ahead"
  }

  /// The phrase for the sector with the strongest active alert that has a
  /// person/object label, or nil. Sectors without a vibrating motor are not
  /// spoken: this follows the belt, it doesn't narrate the scene.
  static func phrase(for status: HubStatus) -> Announcement? {
    guard let sectors = status.sectors, sectors.count == status.levels.count else { return nil }
    var best: (index: Int, level: Int, kind: String)?
    for (index, sector) in sectors.enumerated() {
      let level = status.levels[index]
      guard level > 0, let kind = sector.kind, kind == "person" || kind == "object" else { continue }
      if best == nil || level > best!.level { best = (index, level, kind) }
    }
    guard let best, let direction = direction(index: best.index, count: sectors.count) else { return nil }
    let noun = best.kind == "person" ? "Person" : "Object"
    return Announcement(key: "\(best.kind):\(direction)", text: "\(noun) \(direction)")
  }
}

/// Limits for the optional obstacle-kind speech.
struct SectorSpeechGate: Sendable {
  /// Between any two phrases.
  var minInterval: TimeInterval = 4
  /// Before the same phrase is repeated.
  var repeatInterval: TimeInterval = 15
  /// Quiet time after a state announcement, so the two don't talk over each other.
  var quietAfterState: TimeInterval = 2

  private(set) var lastAt: TimeInterval?
  private(set) var lastSaid: [String: TimeInterval] = [:]
  private(set) var stateAt: TimeInterval?

  mutating func noteStateAnnouncement(now: TimeInterval) { stateAt = now }

  mutating func submit(_ phrase: Announcement, now: TimeInterval) -> Announcement? {
    if let stateAt, now - stateAt < quietAfterState { return nil }
    if let lastAt, now - lastAt < minInterval { return nil }
    if let said = lastSaid[phrase.key], now - said < repeatInterval { return nil }
    lastAt = now
    lastSaid[phrase.key] = now
    lastSaid = lastSaid.filter { now - $0.value < repeatInterval }
    return phrase
  }
}

/// Turns a stream of wearer conditions into state announcements, through one
/// `AnnouncementGate`. "Recovered" / "Resumed" are worded against what the
/// wearer last heard, not against a short-lived state the rate limit swallowed.
/// Quiet states (the short resume cue, `.stopping`) are shown but never spoken,
/// and they leave what was heard and anything held untouched.
struct WearerAnnouncer: Sendable {
  private(set) var gate: AnnouncementGate
  private(set) var shown: WearerCondition
  /// The condition whose announcement the wearer last heard, and the one held by the gate.
  private var spoken: WearerCondition?
  private var held: WearerCondition?

  /// `initial` is the state on screen at launch, which VoiceOver reads anyway.
  init(initial: WearerCondition, minInterval: TimeInterval = 3) {
    gate = AnnouncementGate(minInterval: minInterval, initialKey: initial.kind.key)
    shown = initial
  }

  /// Call on every update. Returns what to speak now, if anything, and the tone for its haptic.
  mutating func update(_ new: WearerCondition, now: TimeInterval) -> (announcement: Announcement, tone: WearerTone)? {
    if new != shown {
      let heard = spoken ?? shown
      shown = new
      if new.kind.isQuiet {
        // Quiet: keep what was heard and anything held.
      } else if let text = WearerStatus.announcement(from: heard, to: new) {
        if let out = gate.submit(Announcement(key: new.kind.key, text: text), now: now) {
          spoken = new
          held = nil
          return (out, new.tone)
        }
        held = gate.pending == nil ? nil : new
      } else {
        // Back to what the wearer last heard: drop anything held back.
        gate.dropPending()
        held = nil
      }
    }
    if let out = gate.poll(now: now) {
      spoken = held ?? shown
      held = nil
      return (out, shown.tone)
    }
    return nil
  }

  /// A one-off message (setup or control errors) through the same rate limit.
  mutating func say(_ announcement: Announcement, now: TimeInterval) -> Announcement? {
    gate.submit(announcement, now: now)
  }
}
