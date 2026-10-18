package cz.cztvhub.core.dvb

/**
 * An ad break of a gateway recording (spec/ad-breaks.md). [start] and [end] are seconds from the
 * beginning of the recording, i.e. positions in its HLS.
 */
data class AdBreak(val start: Double, val end: Double, val confidence: Double = 1.0)

/** The "Přeskočit reklamu" rule shared with the other apps (spec/fixtures/ads/skip_cases.json). */
object AdBreaks {
    /** The button is not offered this close to a break's end. */
    const val MIN_REMAINING_SECONDS = 3.0

    /** Where to skip to from [positionSeconds], or null when the button should be hidden. */
    fun skipTarget(breaks: List<AdBreak>, positionSeconds: Double): Double? =
        breaks.firstOrNull { positionSeconds >= it.start && positionSeconds < it.end - MIN_REMAINING_SECONDS }?.end
}
