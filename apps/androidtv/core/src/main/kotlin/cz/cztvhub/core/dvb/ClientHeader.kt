package cz.cztvhub.core.dvb

/**
 * `X-Cztvhub-Client` (T37, spec/dvb-gateway.md "Sign-in log"): which app and version connects or signs in. Sent
 * **only** on the profile requests (`/dvb/profiles*`, `/dvb/profile*`, [cz.cztvhub.core.profile.ProfileClient]),
 * `GET /dvb/status` ([DvbClient.status]) and the watch reports `POST /dvb/watch` ([WatchReporter], T53); never on
 * other gateway requests (never on playback), never to ČT or the Chamber's CDN.
 */
object ClientHeader {
    const val NAME = "X-Cztvhub-Client"
    const val PLATFORM = "androidtv"

    /** What the gateway accepts (it ignores anything else): at most [MAX_LENGTH] characters of `[A-Za-z0-9 ./()+_-]`. */
    const val MAX_LENGTH = 64
    private val DISALLOWED = Regex("[^A-Za-z0-9 ./()+_-]")

    /**
     * `androidtv/<version> (<build>)` (spec/apps.md "Sign-in note (T37)"); without a version or build (a development
     * build) only what is known, down to just `androidtv`. Disallowed characters are dropped, the value is cut to
     * [MAX_LENGTH], so the gateway never ignores it.
     */
    fun value(version: String?, build: String?): String {
        val v = version?.replace(DISALLOWED, "")?.trim().orEmpty()
        val b = build?.replace(DISALLOWED, "")?.trim().orEmpty()
        val text = when {
            v.isEmpty() -> PLATFORM
            b.isEmpty() -> "$PLATFORM/$v"
            else -> "$PLATFORM/$v ($b)"
        }
        return text.take(MAX_LENGTH)
    }

    /** The header for [value], or none. */
    fun of(value: String?): Map<String, String> = if (value != null) mapOf(NAME to value) else emptyMap()
}
