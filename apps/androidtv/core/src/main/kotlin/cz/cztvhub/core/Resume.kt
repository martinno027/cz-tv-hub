package cz.cztvhub.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
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

    /** Cards in the archive's "Pokračovat ve sledování" row. */
    const val ROW_SIZE = 20

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

    /**
     * The archive's "Pokračovat ve sledování" row (golden `archive_cases.json` "continueRow"): entries with a card
     * title, newest first, at most [ROW_SIZE].
     */
    fun continueRow(entries: Map<String, ResumeEntry>): List<ContinueItem> = entries
        .filter { it.value.card != null }
        .map { ContinueItem(it.key, it.value) }
        .sortedByDescending { it.entry.savedAt }
        .take(ROW_SIZE)

    /** `ct` / `dvb` of a resume key, as stored with its card. */
    fun source(key: String): String = key.substringBefore(':')

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

/**
 * What the "Pokračovat ve sledování" row shows of an entry (spec/apps.md "Archiv", T13), kept on the device only:
 * [title] (programme or episode), [showTitle], [image] (a card image URL or a gateway path, never a full gateway
 * URL) and [source] (`ct` / `dvb`). [start] and [channelId] let the row play a CT programme by the decision rules
 * (within 3 h of its start from the channel's buffer), as the guide would.
 */
data class ResumeCard(
    val title: String,
    val showTitle: String? = null,
    val image: String? = null,
    val source: String,
    val start: Instant? = null,
    val channelId: String? = null,
)

/** One remembered position; [card] null for entries saved before T13 (left out of the row). */
data class ResumeEntry(
    val progressSeconds: Double,
    val lengthSeconds: Double,
    val savedAt: Instant,
    val card: ResumeCard? = null,
) {
    /** The pre-T13 `progress;length;savedAt` without a card, JSON with one (titles can hold any character). */
    fun encode(): String {
        val card = card ?: return "$progressSeconds;$lengthSeconds;${savedAt.toEpochMilli()}"
        return buildJsonObject {
            put("progress", progressSeconds)
            put("length", lengthSeconds)
            put("savedAt", savedAt.toEpochMilli())
            put("title", card.title)
            card.showTitle?.let { put("showTitle", it) }
            card.image?.let { put("image", it) }
            put("source", card.source)
            card.start?.let { put("start", it.toEpochMilli()) }
            card.channelId?.let { put("channel", it) }
        }.toString()
    }

    companion object {
        fun decode(text: String): ResumeEntry? {
            if (text.startsWith("{")) return decodeJson(text)
            val parts = text.split(';')
            if (parts.size != 3) return null
            val progress = parts[0].toDoubleOrNull() ?: return null
            val length = parts[1].toDoubleOrNull() ?: return null
            val saved = parts[2].toLongOrNull() ?: return null
            return ResumeEntry(progress, length, Instant.ofEpochMilli(saved))
        }

        private fun decodeJson(text: String): ResumeEntry? {
            val o = try {
                Json.parseToJsonElement(text) as? JsonObject
            } catch (_: Exception) {
                null
            } ?: return null
            fun str(key: String) = (o[key] as? JsonPrimitive)?.takeIf { it.isString }?.content?.ifEmpty { null }
            fun num(key: String) = (o[key] as? JsonPrimitive)?.takeIf { !it.isString }?.content?.toDoubleOrNull()
            val progress = num("progress") ?: return null
            val length = num("length") ?: return null
            val saved = num("savedAt")?.toLong() ?: return null
            val card = str("title")?.let { title ->
                ResumeCard(
                    title, str("showTitle"), str("image"), str("source").orEmpty(),
                    num("start")?.toLong()?.let(Instant::ofEpochMilli), str("channel"),
                )
            }
            return ResumeEntry(progress, length, Instant.ofEpochMilli(saved), card)
        }
    }
}

/** A card of the "Pokračovat ve sledování" row: the resume [key] and its entry (with a card). */
data class ContinueItem(val key: String, val entry: ResumeEntry) {
    val card: ResumeCard get() = entry.card!!
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
    fun save(key: String, progressSeconds: Double, lengthSeconds: Double, card: ResumeCard? = null) {
        when (ResumeRules.action(progressSeconds, lengthSeconds)) {
            ResumeAction.CLEAR -> backend.remove(listOf(key))
            ResumeAction.NONE -> Unit
            ResumeAction.OFFER -> {
                // A save without a card (e.g. the timeline's playhead moved into a programme) keeps the stored one.
                val kept = card ?: entry(key)?.card
                backend.put(key, ResumeEntry(progressSeconds, lengthSeconds, clock(), kept).encode())
                prune()
            }
        }
    }

    /** "Pokračovat ve sledování": entries that can be offered and have a card, newest first, at most 20. */
    fun continueRow(): List<ContinueItem> {
        val now = clock()
        val entries = backend.all().mapNotNull { (key, value) ->
            val entry = ResumeEntry.decode(value) ?: return@mapNotNull null
            if (isExpired(entry, now)) return@mapNotNull null
            if (ResumeRules.action(entry.progressSeconds, entry.lengthSeconds) != ResumeAction.OFFER) return@mapNotNull null
            key to entry
        }.toMap()
        return ResumeRules.continueRow(entries)
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
