package cz.cztvhub.tv

import android.app.Application
import android.content.Context
import cz.cztvhub.core.CtApi
import cz.cztvhub.core.LiveResolver
import cz.cztvhub.core.Planner
import cz.cztvhub.core.PspMonitor
import cz.cztvhub.core.ResumeStore
import cz.cztvhub.core.ScheduleRepository
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbRepository
import cz.cztvhub.tv.dvb.DvbSettings
import cz.cztvhub.tv.ui.ImageCache

/** Process-wide objects; in memory only, except "Pokračovat" positions and "Kanály" (no stream URL is ever persisted). */
class TvApp : Application() {
    val api by lazy { CtApi() }
    val schedules by lazy { ScheduleRepository(api) }
    val dvbSettings by lazy { DvbSettings(this) }
    val planner by lazy { Planner(api, LiveResolver(api), dvb = { dvb()?.client }) }
    val images by lazy { ImageCache() }

    /**
     * Poslanecká sněmovna: the last "is it on now?" answers, in memory only. Checked only while Živě or the guide
     * is on screen (spec/apps.md); the player just reads it.
     */
    val psp by lazy { PspMonitor() }

    /** "Pokračovat": remembered positions, on this device only. */
    val resume by lazy { ResumeStore(ResumePrefs(this)) }

    /** "Kanály": hidden channels and their order, on this device only. */
    val channelPrefs by lazy { ChannelPrefsStore(this) }

    /** "Náhledy anténních pořadů": this TV's own switch for gateway thumbnails. */
    val thumbnailPrefs by lazy { ThumbnailPrefs(this) }

    private var dvbRepository: DvbRepository? = null

    /** Repository for the configured gateway, or null when "Anténa" is not set up. */
    @Synchronized
    fun dvb(): DvbRepository? {
        val config = dvbSettings.config.value ?: return null.also { dvbRepository = null }
        dvbRepository?.let { if (it.client.config == config) return it }
        return DvbRepository(DvbClient(config)).also { dvbRepository = it }
    }
}

val Context.tvApp: TvApp get() = applicationContext as TvApp
