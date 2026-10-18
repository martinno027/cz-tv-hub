package cz.cztvhub.tv

import android.app.Application
import android.content.Context
import cz.cztvhub.core.CtApi
import cz.cztvhub.core.LiveResolver
import cz.cztvhub.core.Planner
import cz.cztvhub.core.ScheduleRepository
import cz.cztvhub.core.dvb.DvbClient
import cz.cztvhub.core.dvb.DvbRepository
import cz.cztvhub.tv.dvb.DvbSettings
import cz.cztvhub.tv.ui.ImageCache

/** Process-wide objects; everything is in memory only (no stream URL is ever persisted). */
class TvApp : Application() {
    val api by lazy { CtApi() }
    val schedules by lazy { ScheduleRepository(api) }
    val dvbSettings by lazy { DvbSettings(this) }
    val planner by lazy { Planner(api, LiveResolver(api), dvb = { dvb()?.client }) }
    val images by lazy { ImageCache() }

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
