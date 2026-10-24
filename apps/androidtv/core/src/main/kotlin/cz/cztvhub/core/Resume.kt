package cz.cztvhub.core

import java.time.Duration
import java.time.Instant

/** What the remembered progress of a programme allows (spec/apps.md, "Pokračovat"). */
enum class ResumeAction {
    /** Offer "Pokračovat od <label>". */
    OFFER,

    /** Watched: remove the entry. */
    CLEAR,

    /** Too little to remember or offer. */
    NONE,
}

/** Pure rules of "Pokračovat"; golden cases in spec/fixtures/resume_cases.json. Times are seconds. */
object ResumeRules {
    const val MIN_SECONDS = 60.0
    const val END_MARGIN_SECONDS = 120.0
    const val MAX_ENTRIES = 200
    const val MAX_AGE_DAYS = 30L

    fun action(progressSeconds: Double, lengthSeconds: Double): ResumeAction = when {
        progressSeconds >= lengthSeconds - END_MARGIN_SECONDS -> ResumeAction.CLEAR
        progressSeconds >= MIN_SECONDS -> ResumeAction.OFFER
        else -> ResumeAction.NONE
    }

    /** `m:ss` below an hour, else `h:mm:ss`; fractions of a second are dropped. */
    fun label(progressSeconds: Double): String {
        val total = maxOf(0L, progressSeconds.toLong())
        val h = total / 3600
        val m = total % 3600 / 60
        val s = total % 60
        fun two(n: Long) = n.toString().padStart(2, '0')
        return if (h > 0) "$h:${two(m)}:${two(s)}" else "$m:${two(s)}"
    }

    fun ctKey(idec: String): String = "ct:$idec"

    fun dvbKey(recordingId: String): String = "dvb:$recordingId"

    /** The wall-clock moment to resume a CT timeshift at: `start + progress`. */
    fun wallClock(start: Instant, progressSeconds: Double): Instant =
        start.plusMillis((progressSeconds * 1000).toLong())

    /** Fallback buffer offset for players without wall clock: `max(0, (start + progress) − (now − W))`. */
    fun timeshiftOffsetSeconds(start: Instant, progressSeconds: Double, now: Instant): Long {
        val bufferStart = now.minus(Decisions.TIMESHIFT_WINDOW)
        return maxOf(0L, Duration.between(bufferStart, wallClock(start, progressSeconds)).seconds)
    }

    /** Where the progress of [request] is remembered, or null (live is never remembered). */
    fun spot(request: PlayRequest): ResumeSpot? = when (request) {
        is PlayRequest.Programme -> ResumeSpot(ctKey(request.idec), request.start, seconds(request.start, request.end))
        is PlayRequest.Startover -> {
            val idec = request.idec
            val end = request.end
            if (idec == null || end == null) null else ResumeSpot(ctKey(idec), request.start, seconds(request.start, end))
        }
        is PlayRequest.DvbRecorded -> request.lengthSeconds?.let { ResumeSpot(dvbKey(request.recordingId), null, it) }
        is PlayRequest.Live, is PlayRequest.DvbLive, is PlayRequest.PspLive -> null
    }

    private fun seconds(start: Instant, end: Instant): Double = Duration.between(start, end).toMillis() / 1000.0
}

/** A remembered programme: [key] in the store, its [start] (CT, for wall-clock progress) and length. */
data class ResumeSpot(val key: String, val start: Instant?, val lengthSeconds: Double)

/** One remembered position. */
data class ResumeEntry(val progressSeconds: Double, val lengthSeconds: Double, val savedAt: Instant) {
    fun encode(): String = "$progressSeconds;$lengthSeconds;${savedAt.toEpochMilli()}"

    companion object {
        fun decode(text: String): ResumeEntry? {
            val parts = text.split(';')
            if (parts.size != 3) return null
            val progress = parts[0].toDoubleOrNull() ?: return null
            val length = parts[1].toDoubleOrNull() ?: return null
            val saved = parts[2].toLongOrNull() ?: return null
            return ResumeEntry(progress, length, Instant.ofEpochMilli(saved))
        }
    }
}

/** Minimal persistent string map (SharedPreferences on the device, a map in tests). */
interface KeyValueBackend {
    fun all(): Map<String, String>
    fun put(key: String, value: String)
    fun remove(keys: Collection<String>)
}

class MemoryBackend : KeyValueBackend {
    val values = linkedMapOf<String, String>()
    override fun all(): Map<String, String> = values.toMap()
    override fun put(key: String, value: String) {
        values[key] = value
    }
    override fun remove(keys: Collection<String>) {
        keys.forEach { values.remove(it) }
    }
}

/** Positions kept on the device only: no URLs, no video, nothing sent anywhere. */
class ResumeStore(
    private val backend: KeyValueBackend,
    private val clock: () -> Instant = Instant::now,
) {
    fun entry(key: String): ResumeEntry? {
        val entry = backend.all()[key]?.let(ResumeEntry::decode) ?: return null
        return if (isExpired(entry, clock())) null else entry
    }

    /** Progress to offer "Pokračovat" from, or null. */
    fun offer(key: String): Double? {
        val entry = entry(key) ?: return null
        return entry.progressSeconds.takeIf {
            ResumeRules.action(it, entry.lengthSeconds) == ResumeAction.OFFER
        }
    }

    /**
     * Remember [progressSeconds]: stored when it can be offered, removed when watched; below the minimum an
     * existing entry is kept (starting over by mistake does not lose the resume point).
     */
    fun save(key: String, progressSeconds: Double, lengthSeconds: Double) {
        when (ResumeRules.action(progressSeconds, lengthSeconds)) {
            ResumeAction.CLEAR -> backend.remove(listOf(key))
            ResumeAction.NONE -> Unit
            ResumeAction.OFFER -> {
                backend.put(key, ResumeEntry(progressSeconds, lengthSeconds, clock()).encode())
                prune()
            }
        }
    }

    fun remove(key: String) = backend.remove(listOf(key))

    /** Drops entries older than [ResumeRules.MAX_AGE_DAYS] or unreadable, then keeps the newest [ResumeRules.MAX_ENTRIES]. */
    fun prune() {
        val now = clock()
        val drop = mutableListOf<String>()
        val kept = mutableListOf<Pair<String, ResumeEntry>>()
        for ((key, value) in backend.all()) {
            val entry = ResumeEntry.decode(value)
            if (entry == null || isExpired(entry, now)) drop += key else kept += key to entry
        }
        if (kept.size > ResumeRules.MAX_ENTRIES) {
            drop += kept.sortedByDescending { it.second.savedAt }.drop(ResumeRules.MAX_ENTRIES).map { it.first }
        }
        if (drop.isNotEmpty()) backend.remove(drop)
    }

    private fun isExpired(entry: ResumeEntry, now: Instant): Boolean =
        entry.savedAt.isBefore(now.minus(Duration.ofDays(ResumeRules.MAX_AGE_DAYS)))
}
