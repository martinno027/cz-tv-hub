package cz.cztvhub.tv

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import cz.cztvhub.core.OfficialApp

/** Opens CT's iVysílání app, or its store page. Returns a Czech message when neither is possible. */
object OfficialAppLauncher {
    fun open(context: Context): String? {
        val pm = context.packageManager
        val launch = pm.getLeanbackLaunchIntentForPackage(OfficialApp.PACKAGE)
            ?: pm.getLaunchIntentForPackage(OfficialApp.PACKAGE)
        val market = Intent(Intent.ACTION_VIEW, Uri.parse(OfficialApp.MARKET_URL))
        val web = Intent(Intent.ACTION_VIEW, Uri.parse(OfficialApp.WEB_URL))
        val intent = when (
            OfficialApp.choose(
                canLaunchApp = launch != null,
                canOpenMarket = market.resolveActivity(pm) != null,
                canOpenWeb = web.resolveActivity(pm) != null,
            )
        ) {
            OfficialApp.Target.LAUNCH_APP -> launch
            OfficialApp.Target.STORE_APP -> market
            OfficialApp.Target.STORE_WEB -> web
            OfficialApp.Target.NONE -> null
        } ?: return OfficialApp.NOT_AVAILABLE
        return try {
            context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            null
        } catch (_: ActivityNotFoundException) {
            OfficialApp.NOT_AVAILABLE
        }
    }
}
