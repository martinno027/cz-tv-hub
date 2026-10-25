package cz.cztvhub.tv

import android.app.Activity
import android.app.Application
import android.content.Context
import android.os.Bundle
import cz.cztvhub.core.BridgeWatch
import cz.cztvhub.core.CtApi
import cz.cztvhub.core.LiveResolver
import cz.cztvhub.core.Planner
import cz.cztvhub.core.PspMonitor
import cz.cztvhub.core.ResumeStore
import cz.cztvhub.core.ScheduleRepository
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbConfig
import cz.cztvhub.core.dvb.DvbRepository
import cz.cztvhub.core.dvb.UrlConnectionDvbTransport
import cz.cztvhub.core.profile.ProfileClient
import cz.cztvhub.core.profile.UrlConnectionProfileTransport
import cz.cztvhub.tv.dvb.DvbSettings
import cz.cztvhub.tv.dvb.TailnetController
import cz.cztvhub.tv.ui.ImageCache
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn

/** Process-wide objects; in memory only, except "Pokračovat" positions and "Kanály" (no stream URL is ever persisted). */
class TvApp : Application() {
    val api by lazy { CtApi() }
    val schedules by lazy { ScheduleRepository(api) }
    val dvbSettings by lazy { DvbSettings(this) }

    /** "Připojovat přes Tailscale": the in-app tailnet node and its bridge on 127.0.0.1. */
    val tailnet by lazy { TailnetController(this, dvbSettings) }

    /**
     * The gateway as everything talking to it uses it (API, player, thumbnails, profile): the saved address, or the
     * Tailscale bridge with the same key; null while the bridge is starting (no request goes around it).
     */
    val gateway: StateFlow<DvbConfig?> by lazy {
        combine(dvbSettings.config, tailnet.state) { config, _ -> route(config) }
            .stateIn(CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate), SharingStarted.Eagerly, route(dvbSettings.config.value))
    }

    /** [config] (the saved one, or one being checked in Nastavení) as reached now. */
    fun route(config: DvbConfig?): DvbConfig? = when (val route = tailnet.state.value.route) {
        TailnetController.Route.Direct -> config
        TailnetController.Route.Pending -> null
        is TailnetController.Route.Bridge -> config?.copy(baseUrl = route.baseUrl)
    }

    /** A gateway request through the bridge that the bridge could not pass on makes the app check it (T18). */
    private val bridgeWatch = BridgeWatch { tailnet.checkBridge() }

    /** True when [config] goes through the Tailscale bridge (its requests are watched). */
    private fun bridged(config: DvbConfig): Boolean =
        (tailnet.state.value.route as? TailnetController.Route.Bridge)?.baseUrl == config.baseUrl

    val planner by lazy { Planner(api, LiveResolver(api), dvb = { dvb()?.client }) }
    val images by lazy { ImageCache() }

    /**
     * Poslanecká sněmovna: the last "is it on now?" answers, in memory only. Checked only while Živě or the guide
     * is on screen (spec/apps.md); the player just reads it.
     */
    val psp by lazy { PspMonitor() }

    /** "Pokračovat": remembered positions, on this device only. */
    val resume by lazy { ResumeStore(ResumePrefs(this)) }

    /** "Kanály": hidden channels and their order, this TV's own or a signed-in profile's. */
    val channelPrefs by lazy { ChannelPrefsStore(this) }

    /** "Náhledy anténních pořadů": the switch for gateway thumbnails, this TV's own or a signed-in profile's. */
    val thumbnailPrefs by lazy { ThumbnailPrefs(this) }

    /** "Profil": the settings above kept in a profile on the gateway while signed in. */
    val profiles by lazy { ProfileManager(this, dvbSettings, channelPrefs, thumbnailPrefs) {
            gateway.value?.let { config ->
                val transport = UrlConnectionProfileTransport()
                ProfileClient(config, if (bridged(config)) bridgeWatch.profile(transport) else transport)
            }
        } }

    /** The household default channel list from the gateway, shown while signed out and the list is not the TV's own. */
    val defaults by lazy { HouseholdDefaultManager(this, this) }

    private var dvbRepository: DvbRepository? = null

    override fun onCreate() {
        super.onCreate()
        tailnet // with the switch on, the node starts before the first gateway request
        registerActivityLifecycleCallbacks(Foreground { tailnet.checkBridge() })
    }

    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        if (level == TRIM_MEMORY_UI_HIDDEN) tailnet.saveNow()
    }

    /** Repository for the configured gateway, or null when "Anténa" is not set up. */
    @Synchronized
    fun dvb(): DvbRepository? {
        val config = gateway.value ?: return null.also { dvbRepository = null }
        dvbRepository?.let { if (it.client.config == config) return it }
        val transport = UrlConnectionDvbTransport()
        return DvbRepository(DvbClient(config, if (bridged(config)) bridgeWatch.dvb(transport) else transport))
            .also { dvbRepository = it }
    }

    /** Calls [onFront] when the app comes to the front (its first activity starts), also from the background. */
    private class Foreground(private val onFront: () -> Unit) : ActivityLifecycleCallbacks {
        private var started = 0

        override fun onActivityStarted(activity: Activity) {
            if (started++ == 0) onFront()
        }

        override fun onActivityStopped(activity: Activity) {
            started--
        }

        override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
        override fun onActivityResumed(activity: Activity) {}
        override fun onActivityPaused(activity: Activity) {}
        override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
        override fun onActivityDestroyed(activity: Activity) {}
    }
}

val Context.tvApp: TvApp get() = applicationContext as TvApp
