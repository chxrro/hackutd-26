import Foundation

/// Existing ESP32 v1 protocol. Always five motors for this app's sector map.
enum BeltProtocol {
  static let service = "88368a33-70aa-41e2-b071-4f618af1856a"
  static let command = "71dbf1b6-d7d2-4208-954b-b8a38e54618f"
  static let info = "986b42ae-86e7-4eb6-a579-cd8360e40c0b"
  static let status = "ba2928d9-3014-49de-8949-bd9933b65d7a"
  static let motors = 5
  static let ttlMs: UInt16 = 250
}

struct BeltCommand: Equatable, Sendable {
  enum Mode: UInt8, Sendable { case off = 0, levels = 1, fault = 2 }
  let mode: Mode
  let levels: [UInt8]
  private init(mode: Mode, levels: [UInt8]) { self.mode = mode; self.levels = levels }
  static let off = Self(mode: .off, levels: Array(repeating: 0, count: 5))
  static let fault = Self(mode: .fault, levels: Array(repeating: 0, count: 5))

  static func levels(_ values: [Int]) -> Self? {
    guard values.count == 5, values.allSatisfy({ (0...255).contains($0) }) else { return nil }
    return Self(mode: .levels, levels: values.map(UInt8.init))
  }

  func encode(seq: UInt8) -> Data {
    Data([1, mode.rawValue, seq, UInt8(BeltProtocol.ttlMs & 0xff), UInt8(BeltProtocol.ttlMs >> 8), 5] + levels)
  }
}

struct BeltInfo: Equatable, Sendable {
  let build: String
  static func decode(_ data: Data) -> Self? {
    let b = Array(data)
    guard (2...18).contains(b.count), b[0] == 1, b[1] == 5,
      b.dropFirst(2).allSatisfy({ (32...126).contains($0) }) else { return nil }
    return Self(build: b.count > 2 ? String(decoding: b.dropFirst(2), as: UTF8.self) : "unknown")
  }
}

struct BeltFirmwareStatus: Equatable, Sendable {
  let mode: UInt8
  let seq: UInt8
  let commandAgeMs: UInt16
  let uptimeMs: UInt32
  let buildID: UInt32
  let maxDuty: UInt8
  let levels: [UInt8]
  var modeName: String { ["idle", "off", "levels", "fault", "expired", "disconnected", "dormant"][Int(mode)] }

  static func decode(_ data: Data) -> Self? {
    let b = Array(data)
    guard b.count == 20, b[0] == 1, b[1] <= 6, b[14] == 5 else { return nil }
    func u32(_ at: Int) -> UInt32 {
      (0..<4).reduce(UInt32(0)) { $0 | UInt32(b[at + $1]) << ($1 * 8) }
    }
    return Self(mode: b[1], seq: b[2], commandAgeMs: UInt16(b[3]) | UInt16(b[4]) << 8,
                uptimeMs: u32(5), buildID: u32(9), maxDuty: b[13], levels: Array(b[15...]))
  }
}
