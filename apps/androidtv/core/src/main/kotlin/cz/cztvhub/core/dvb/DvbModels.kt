package cz.cztvhub.core.dvb

import cz.cztvhub.core.Timed
import java.time.Instant

/** A channel of the household's own DVB-T2 tuner, served by the cz-tv-hub gateway ("Anténa"). */
data class DvbChannel(val id: String, val number: Int, val name: String, val iconPath: String?)

enum class RecordingState { COMPLETED, RECORDING, SCHEDULED, FAILED }

data class DvbRecording(val id: String, val state: RecordingState)

/** An EPG event. [id] is opaque (past events look like `dvr:<uuid>`). */
data class DvbEvent(
    val id: String,
    val channelId: String,
    val title: String,
    override val start: Instant,
    override val end: Instant,
    val subtitle: String = "",
    val description: String = "",
    val genre: String = "",
    val recording: DvbRecording? = null,
) : Timed {
    fun isAiring(now: Instant): Boolean = !now.isBefore(start) && now.isBefore(end)
}

data class DvbTuner(val name: String, val inUse: Boolean)

data class DvbStatus(
    val enabled: Boolean,
    val retentionDays: Int,
    val tuners: List<DvbTuner>,
    val tvheadendReachable: Boolean,
)

object DvbMessages {
    const val UNREACHABLE = "Brána antény není dostupná"
    const val BAD_KEY = "Chybný klíč brány"
    const val NOT_RECORDED = "Pořad nebyl nahrán"
    const val GENERIC = "Anténu se nepodařilo načíst"
    const val HOST_NOT_ALLOWED = "Adresa brány musí být v domácí síti nebo v tailnetu"
}
