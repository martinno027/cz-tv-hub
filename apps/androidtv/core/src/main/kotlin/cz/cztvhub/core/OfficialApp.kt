package cz.cztvhub.core

/**
 * CT's official iVysílání app, offered for programmes this app cannot play (e.g. DRM-only).
 * Only the app itself is opened; no deep links into programmes are guessed.
 */
object OfficialApp {
    const val PACKAGE = "cz.motion.ivysilani"
    const val MARKET_URL = "market://details?id=$PACKAGE"
    const val WEB_URL = "https://play.google.com/store/apps/details?id=$PACKAGE"
    const val NOT_AVAILABLE = "Nainstalujte aplikaci iVysílání od České televize z Obchodu Play."

    enum class Target { LAUNCH_APP, STORE_APP, STORE_WEB, NONE }

    /** First option that some activity on the device can handle. */
    fun choose(canLaunchApp: Boolean, canOpenMarket: Boolean, canOpenWeb: Boolean): Target = when {
        canLaunchApp -> Target.LAUNCH_APP
        canOpenMarket -> Target.STORE_APP
        canOpenWeb -> Target.STORE_WEB
        else -> Target.NONE
    }
}
