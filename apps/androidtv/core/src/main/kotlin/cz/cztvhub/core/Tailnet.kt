package cz.cztvhub.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.longOrNull

/**
 * The bridge's `Info()` (spec/apps.md "Tailscale v aplikaci"): Tailscale's backend state as sent; an unknown state stays
 * a plain string and shows as "connecting".
 */
data class TailnetInfo(
    val state: String,
    val loginUrl: String = "",
    val self: String = "",
    val ip: String = "",
    val stateVersion: Long = 0,
) {
    companion object {
        /** Null when the text is not the expected JSON object. */
        fun parse(json: String): TailnetInfo? {
            val o = try {
                Json.parseToJsonElement(json).jsonObject
            } catch (_: Exception) {
                return null
            }
            return TailnetInfo(
                o.text("state"),
                o.text("loginURL"),
                o.text("self"),
                o.text("ip"),
                (o["stateVersion"] as? JsonPrimitive)?.longOrNull ?: 0,
            )
        }

        private fun JsonObject.text(key: String): String =
            (this[key] as? JsonPrimitive)?.takeIf { it.isString }?.content.orEmpty()
    }
}

/** What Nastavení → Server shows for Tailscale (golden cases `tailnet_cases.json` "status"). */
data class TailnetStatus(val line: String, val qr: Boolean, val logout: Boolean, val usesBridge: Boolean) {
    companion object {
        const val OFF = "Vypnuto"
        const val CONNECTING = "Připojuji se k Tailscale…"
        const val SCAN = "Naskenujte QR kód telefonem a přihlaste se do Tailscale."
        const val NEEDS_APPROVAL = "Televize čeká na schválení v administraci Tailscale."

        /** The switch is on but no gateway address is saved, so there is nothing to bridge to. */
        const val NO_TARGET = "chybí adresa brány."

        fun failed(error: String) = "Tailscale se nepodařilo spustit: $error"

        /**
         * [info] is null until the node started; [error] is the start failure. The bridge is used as soon as the
         * node started (before `Running` it answers 502 with the gateway-style error).
         */
        fun of(enabled: Boolean, info: TailnetInfo?, error: String?): TailnetStatus {
            if (!enabled) return TailnetStatus(OFF, qr = false, logout = false, usesBridge = false)
            if (error != null) return TailnetStatus(failed(error), qr = false, logout = false, usesBridge = false)
            if (info == null) return TailnetStatus(CONNECTING, qr = false, logout = false, usesBridge = false)
            val qr = info.state == "NeedsLogin" && info.loginUrl.isNotEmpty()
            val line = when {
                info.state == "Running" -> "Připojeno jako ${info.self}" + if (info.ip.isEmpty()) "" else " (${info.ip})"
                qr -> SCAN
                info.state == "NeedsMachineAuth" -> NEEDS_APPROVAL
                else -> CONNECTING
            }
            // A node that has a name has logged in at least once: logging out means something.
            return TailnetStatus(line, qr, logout = info.self.isNotEmpty(), usesBridge = true)
        }
    }
}
