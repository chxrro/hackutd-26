//
// FramePacket.swift
//
// Phone -> hub frame message. Contract: docs/protocol.md section 1.
// Little-endian header (20 bytes) followed by the JPEG bytes.
//

import Foundation

enum FramePacket {
  static let magic: [UInt8] = [0x48, 0x42]  // "HB"
  static let version: UInt8 = 1
  static let headerSize = 20

  static func encode(seq: UInt32, captureMs: UInt64, width: UInt16, height: UInt16, jpeg: Data) -> Data {
    var data = Data(capacity: headerSize + jpeg.count)
    data.append(contentsOf: magic)
    data.append(version)
    data.append(0)  // flags, reserved
    append(seq, to: &data)
    append(captureMs, to: &data)
    append(width, to: &data)
    append(height, to: &data)
    data.append(jpeg)
    return data
  }

  private static func append<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
    withUnsafeBytes(of: value.littleEndian) { data.append(contentsOf: $0) }
  }
}
