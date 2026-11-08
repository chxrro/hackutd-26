import Foundation

enum FrameSource: String, Sendable {
    case test
    case glasses
}

struct FrameUploader: Sendable {
    let endpoint: URL
    let code: String
    let source: FrameSource

    private static let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 2
        config.timeoutIntervalForResource = 2
        config.allowsCellularAccess = false
        return URLSession(configuration: config, delegate: RejectRedirects(), delegateQueue: nil)
    }()

    init(address: String, code: String, source: FrameSource) throws {
        guard var url = URLComponents(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme == "http", url.user == nil, url.password == nil,
              url.path.isEmpty || url.path == "/", url.query == nil, url.fragment == nil,
              let host = url.host?.lowercased(), Self.isLocal(host),
              let port = url.port, (1...65535).contains(port) else {
            throw UploadError.invalidAddress
        }
        let code = code.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard code.utf8.count == 16, code.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
            throw UploadError.invalidCode
        }
        url.path = "/frame"
        guard let endpoint = url.url else { throw UploadError.invalidAddress }
        self.endpoint = endpoint
        self.code = code
        self.source = source
    }

    func send(_ jpeg: Data) async throws {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("Bearer \(code)", forHTTPHeaderField: "Authorization")
        request.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
        request.setValue(source.rawValue, forHTTPHeaderField: "X-Frame-Source")

        let (_, response) = try await Self.session.upload(for: request, from: jpeg)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 204 else { throw UploadError.receiver(status) }
    }

    private static func isLocal(_ host: String) -> Bool {
        if host.hasSuffix(".local") { return true }
        let octets = host.split(separator: ".", omittingEmptySubsequences: false).compactMap { UInt8($0) }
        guard octets.count == 4, octets.map({ String($0) }).joined(separator: ".") == host else { return false }
        return octets[0] == 10 || octets[0] == 127 ||
            (octets[0] == 172 && (16...31).contains(octets[1])) ||
            (octets[0] == 192 && octets[1] == 168)
    }
}

private final class RejectRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession, task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

private enum UploadError: LocalizedError {
    case invalidAddress, invalidCode, receiver(Int)

    var errorDescription: String? {
        switch self {
        case .invalidAddress: return "Enter the computer’s local address, including http:// and the port."
        case .invalidCode: return "Enter the 16-character pairing code from the viewer terminal."
        case .receiver(401): return "The viewer rejected the pairing code. Check the current code in the terminal."
        case .receiver(let status): return "The viewer rejected the frame (HTTP \(status))."
        }
    }
}
