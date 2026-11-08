//
// HubMessages.swift
//
// JSON text messages exchanged with the hub. Contract: docs/protocol.md sections 1-2.
//

import Foundation

/// What the hub is telling the belt to do, sent ~5x per second.
struct HubStatus: Decodable, Equatable, Sendable {
  let mode: String  // "off" | "levels" | "fault"
  let healthy: Bool
  let reason: String?
  let levels: [Int]
  let belt: String  // "connected" | "connecting" | "disconnected" | "simulated"
  let source: String?
  let inferenceMs: Double?
  let frameAgeMs: Double?

  enum CodingKeys: String, CodingKey {
    case type, mode, healthy, reason, levels, belt, source
    case inferenceMs = "inference_ms"
    case frameAgeMs = "frame_age_ms"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    guard try c.decode(String.self, forKey: .type) == "status" else {
      throw DecodingError.dataCorruptedError(forKey: .type, in: c, debugDescription: "not a status message")
    }
    mode = try c.decode(String.self, forKey: .mode)
    healthy = try c.decode(Bool.self, forKey: .healthy)
    reason = try c.decodeIfPresent(String.self, forKey: .reason)
    levels = try c.decode([Int].self, forKey: .levels)
    belt = try c.decode(String.self, forKey: .belt)
    source = try c.decodeIfPresent(String.self, forKey: .source)
    inferenceMs = try c.decodeIfPresent(Double.self, forKey: .inferenceMs)
    frameAgeMs = try c.decodeIfPresent(Double.self, forKey: .frameAgeMs)
  }

  /// Returns nil for anything that isn't a well-formed status message.
  static func parse(_ text: String) -> HubStatus? {
    try? JSONDecoder().decode(HubStatus.self, from: Data(text.utf8))
  }
}

enum HubOutgoing {
  private struct Hello: Encodable {
    let type = "hello"
    let client = "ios"
    let version = 1
  }

  private struct PhoneStatus: Encodable {
    let type = "phone_status"
    let stream: String
  }

  static func hello() -> String { json(Hello()) }
  static func phoneStatus(stream: String) -> String { json(PhoneStatus(stream: stream)) }

  private static func json(_ value: some Encodable) -> String {
    let encoder = JSONEncoder()
    encoder.outputFormatting = .sortedKeys
    // Encoding these fixed structs can't fail.
    return String(decoding: (try? encoder.encode(value)) ?? Data("{}".utf8), as: UTF8.self)
  }
}
