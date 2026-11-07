//
// HubAddress.swift
//
// Parses what the user types into the hub address field.
//

import Foundation

enum HubAddress {
  static let defaultPort = 8765

  /// Accepts "ws://host:port", "host:port" or "host" (default port 8765).
  static func parse(_ text: String) -> URL? {
    var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
    if !s.contains("://") { s = "ws://" + s }
    guard var components = URLComponents(string: s),
      let scheme = components.scheme, ["ws", "wss"].contains(scheme),
      let host = components.host, !host.isEmpty
    else { return nil }
    if components.port == nil { components.port = defaultPort }
    return components.url
  }
}
