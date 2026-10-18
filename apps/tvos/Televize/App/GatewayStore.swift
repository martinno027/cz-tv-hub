import CTCore
import Foundation
import Security

/// Keeps the gateway address and bearer key in the Keychain (never in UserDefaults).
enum GatewayStore {
    private static let service = "cz.cztvhub.tv.gateway"
    private static let account = "default"

    static func load() -> GatewayConfig? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(GatewayConfig.self, from: data)
    }

    @discardableResult
    static func save(_ config: GatewayConfig) -> Bool {
        guard let data = try? JSONEncoder().encode(config) else { return false }
        SecItemDelete(baseQuery as CFDictionary)
        var item = baseQuery
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }

    static func delete() {
        SecItemDelete(baseQuery as CFDictionary)
    }

    private static var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }
}
