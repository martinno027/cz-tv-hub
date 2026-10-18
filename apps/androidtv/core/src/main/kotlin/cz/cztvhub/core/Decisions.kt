package cz.cztvhub.core

import java.time.Duration
import java.time.Instant

enum class Action { FUTURE, STARTOVER, VOD, VOD_UNAVAILABLE }

/** [offsetSeconds] is set for [Action.STARTOVER]: position from the beginning of the rewind buffer. */
data class Decision(val action: Action, val offsetSeconds: Long? = null)

/** Decision rules of spec/apps.md for selecting a programme in the guide. */
object Decisions {
    /** Length of CT's "Zpět v čase" buffer. */
    val TIMESHIFT_WINDOW: Duration = Duration.ofHours(3)

    /** Start a bit early rather than miss the beginning. */
    val START_MARGIN: Duration = Duration.ofSeconds(30)

    /** Decided by time only: anything that began less than [TIMESHIFT_WINDOW] ago plays from the buffer. */
    fun decide(start: Instant, end: Instant, isPlayableNow: Boolean, now: Instant): Decision = when {
        now.isBefore(start) -> Decision(Action.FUTURE)
        now.isBefore(end) || Duration.between(start, now) < TIMESHIFT_WINDOW ->
            Decision(Action.STARTOVER, startoverOffsetSeconds(start, now))
        isPlayableNow -> Decision(Action.VOD)
        else -> Decision(Action.VOD_UNAVAILABLE)
    }

    fun decide(programme: Programme, now: Instant): Decision =
        decide(programme.start, programme.end, programme.isPlayableNow, now)

    /** `max(0, (start − M) − (now − W))` in seconds. */
    fun startoverOffsetSeconds(start: Instant, now: Instant): Long {
        val bufferStart = now.minus(TIMESHIFT_WINDOW)
        return maxOf(0L, Duration.between(bufferStart, start.minus(START_MARGIN)).seconds)
    }
}
