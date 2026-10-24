import CTCore
import Foundation
import Security

/// One JSON value in the Keychain (never in UserDefaults).
struct KeychainItem<Value: Codable> {
    let service: String
    var account = "default"

    func load() -> Value? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(Value.self, from: data)
    }

    @discardableResult
    func save(_ value: Value) -> Bool {
        guard let data = try? JSONEncoder().encode(value) else { return false }
        SecItemDelete(baseQuery as CFDictionary)
        var item = baseQuery
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }

    func delete() {
        SecItemDelete(baseQuery as CFDictionary)
    }

    private var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }
}

/// Keeps the gateway address and bearer key in the Keychain.
enum GatewayStore {
    private static let item = KeychainItem<GatewayConfig>(service: "cz.cztvhub.tv.gateway")

    static func load() -> GatewayConfig? { item.load() }

    @discardableResult
    static func save(_ config: GatewayConfig) -> Bool { item.save(config) }

    static func delete() { item.delete() }
}

/// The signed-in profile's name and session token (spec/apps.md "Profil"); the password is never stored.
@MainActor
final class ProfileKeychain: ProfileCredentialStore {
    private let item = KeychainItem<ProfileCredentials>(service: "cz.cztvhub.tv.profile")

    func load() -> ProfileCredentials? { item.load() }
    func save(_ credentials: ProfileCredentials) { item.save(credentials) }
    func delete() { item.delete() }
}
