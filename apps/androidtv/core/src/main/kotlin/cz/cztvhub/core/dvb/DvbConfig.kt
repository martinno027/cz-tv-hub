package cz.cztvhub.core.dvb

import java.net.URI

/**
 * Where the gateway is and its bearer key. Cleartext HTTP is only allowed to home-LAN, mDNS,
 * Tailscale (MagicDNS names and 100.64.0.0/10) and loopback/emulator hosts; the app never
 * talks plain HTTP to anything else.
 */
data class DvbConfig(val baseUrl: String, val key: String) {
    val authHeaders: Map<String, String> get() = mapOf("Authorization" to "Bearer $key")

    fun url(path: String): String = baseUrl + path

    companion object {
        const val DEFAULT_PORT = 8098
        const val MIN_KEY_LENGTH = 32

        /** `pi`, `pi.tail1234.ts.net`, `192.168.1.5:8098`, `http://…` → `http://host:port`; null if invalid. */
        fun normalizeAddress(address: String): String? {
            val uri = parse(address) ?: return null
            val port = if (uri.port > 0) uri.port else if (uri.scheme == "https") 443 else DEFAULT_PORT
            return "${uri.scheme}://${hostPart(uri.host)}:$port"
        }

        /**
         * The same address as `host:port` for the Tailscale bridge (spec/fixtures/tailnet_cases.json "target"): the
         * bridge speaks plain HTTP to the gateway, so the default port is [DEFAULT_PORT] whatever the scheme.
         */
        fun tailnetTarget(address: String): String? {
            val uri = parse(address) ?: return null
            return "${hostPart(uri.host)}:${if (uri.port > 0) uri.port else DEFAULT_PORT}"
        }

        private fun parse(address: String): URI? {
            val trimmed = address.trim().trimEnd('/')
            if (trimmed.isEmpty()) return null
            val withScheme = if ("://" in trimmed) trimmed else "http://$trimmed"
            val uri = try {
                URI(withScheme)
            } catch (_: Exception) {
                return null
            }
            if (uri.scheme != "http" && uri.scheme != "https") return null
            val host = uri.host ?: return null
            // LAN / tailnet only, whatever the scheme (spec/dvb-gateway.md: never exposed to the internet).
            return uri.takeIf { isPrivateHost(host) }
        }

        private fun hostPart(host: String) = if (':' in host && !host.startsWith("[")) "[$host]" else host

        fun isPrivateHost(host: String): Boolean {
            val h = host.lowercase().trim('[', ']')
            if (h == "localhost" || h.endsWith(".local") || h.endsWith(".ts.net") || h.endsWith(".home.arpa")) return true
            if (!h.contains('.') && !h.contains(':')) return true // single-label LAN / MagicDNS short name
            val parts = h.split('.').mapNotNull { it.toIntOrNull() }
            if (parts.size == 4 && parts.all { it in 0..255 } && h.count { it == '.' } == 3) {
                val (a, b) = parts
                return a == 10 || a == 127 || (a == 192 && b == 168) || (a == 172 && b in 16..31) ||
                    (a == 100 && b in 64..127) || (a == 169 && b == 254)
            }
            if (':' in h) return h == "::1" || h.startsWith("fd") || h.startsWith("fc") || h.startsWith("fe80") ||
                h.startsWith("fd7a:115c:a1e0") // Tailscale IPv6
            return false
        }
    }
}
