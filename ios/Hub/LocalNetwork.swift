import Foundation

/// Address and pairing-code rules shared by the hub link and the diagnostic
/// viewer uploader. These are Ethan's original FrameUploader checks, moved here
/// so both paths validate the same way.
enum LocalNetwork {
    /// `.local` names and private or loopback IPv4 addresses. The links are
    /// unencrypted, so the app refuses anything that looks like the internet.
    static func isLocalHost(_ host: String) -> Bool {
        let host = host.lowercased()
        if host.hasSuffix(".local") { return true }
        let octets = host.split(separator: ".", omittingEmptySubsequences: false).compactMap { UInt8($0) }
        guard octets.count == 4, octets.map({ String($0) }).joined(separator: ".") == host else { return false }
        return octets[0] == 10 || octets[0] == 127 ||
            (octets[0] == 172 && (16...31).contains(octets[1])) ||
            (octets[0] == 192 && octets[1] == 168)
    }

    /// A 16-character hex code (viewer pairing code or hub control code),
    /// trimmed and lowercased, or nil if it isn't one.
    static func normalizedCode(_ text: String) -> String? {
        let code = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard code.utf8.count == 16, code.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
            return nil
        }
        return code
    }
}
