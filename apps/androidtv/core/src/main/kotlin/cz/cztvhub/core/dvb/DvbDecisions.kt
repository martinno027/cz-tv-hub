package cz.cztvhub.core.dvb

import java.time.Instant

/** How an "Anténa" programme can be played (spec/dvb-gateway.md, "App behaviour for Anténa programmes"). */
enum class DvbAction {
    /** Not started yet: "Pořad ještě nezačal". */
    FUTURE,

    /** Airing, no recording in progress: live only. */
    LIVE,

    /** Airing and being recorded: "Živě" or "Od začátku" (the growing recording). */
    LIVE_OR_STARTOVER,

    /** Ended and completely recorded. */
    RECORDING,

    /** Ended without a usable recording: dimmed, "Pořad nebyl nahrán". */
    NOT_RECORDED,
}

object DvbDecisions {
    fun decide(event: DvbEvent, now: Instant): DvbAction {
        val state = event.recording?.state
        return when {
            now.isBefore(event.start) -> DvbAction.FUTURE
            now.isBefore(event.end) ->
                if (state == RecordingState.RECORDING) DvbAction.LIVE_OR_STARTOVER else DvbAction.LIVE
            state == RecordingState.COMPLETED -> DvbAction.RECORDING
            else -> DvbAction.NOT_RECORDED
        }
    }
}
