import Foundation
import Security

/// Remembers the hub address (UserDefaults) and control code (Keychain, this
/// device only). The code changes every time the hub restarts.
enum PairingStore {
    private static let addressKey = "hubAddress"
    private static let service = "CameraBridge.hub"
    private static let account = "control_code"

    static func load() -> (address: String, code: String) {
        (UserDefaults.standard.string(forKey: addressKey) ?? "", readCode() ?? "")
    }

    /// Returns false if the code couldn't be stored (for example in an unsigned
    /// simulator build without Keychain access). The pairing still works until relaunch.
    @discardableResult
    static func save(address: String, code: String) -> Bool {
        UserDefaults.standard.set(address.trimmingCharacters(in: .whitespacesAndNewlines), forKey: addressKey)
        deleteCode()
        let code = code.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !code.isEmpty else { return true }
        var query = baseQuery
        query[kSecValueData as String] = Data(code.utf8)
        query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    static func clear() {
        UserDefaults.standard.removeObject(forKey: addressKey)
        deleteCode()
    }

    private static var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func readCode() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess, let data = item as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    private static func deleteCode() {
        SecItemDelete(baseQuery as CFDictionary)
    }
}
