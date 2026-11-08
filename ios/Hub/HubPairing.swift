import Foundation

/// A validated hub address plus the optional control code the hub prints at startup.
struct HubPairing: Equatable, Sendable {
    static let defaultPort = 8765

    let url: URL
    /// nil = frames only. The hub then refuses pause, settings, belt test and recording.
    let controlCode: String?

    enum Problem: Error, Equatable, LocalizedError {
        case invalidAddress, notLocal, invalidCode

        var errorDescription: String? {
            switch self {
            case .invalidAddress:
                return "Enter the hub address the laptop prints, for example 192.168.1.20 or ws://laptop.local:8765."
            case .notLocal:
                return "Use the laptop’s local address (a .local name or a 10.x, 172.16–31.x, 192.168.x or 127.x address)."
            case .invalidCode:
                return "Enter the 16-character control code from the hub terminal, or leave it empty for frames only."
            }
        }
    }

    /// Accepts "host", "host:port" or "ws://host:port" (default port 8765) on the
    /// local network, and an empty or 16-hex-character control code.
    static func validate(address: String, code: String) -> Result<HubPairing, Problem> {
        var text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.contains("://") { text = "ws://" + text }
        guard var components = URLComponents(string: text),
              components.scheme?.lowercased() == "ws",
              components.user == nil, components.password == nil,
              components.path.isEmpty || components.path == "/",
              components.query == nil, components.fragment == nil,
              let host = components.host, !host.isEmpty else {
            return .failure(.invalidAddress)
        }
        guard LocalNetwork.isLocalHost(host) else { return .failure(.notLocal) }
        if let port = components.port {
            guard (1...65535).contains(port) else { return .failure(.invalidAddress) }
        } else {
            components.port = defaultPort
        }
        components.scheme = "ws"
        components.host = host.lowercased()
        components.path = "/"
        guard let url = components.url else { return .failure(.invalidAddress) }

        let trimmed = code.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return .success(HubPairing(url: url, controlCode: nil)) }
        guard let code = LocalNetwork.normalizedCode(trimmed) else { return .failure(.invalidCode) }
        return .success(HubPairing(url: url, controlCode: code))
    }
}
