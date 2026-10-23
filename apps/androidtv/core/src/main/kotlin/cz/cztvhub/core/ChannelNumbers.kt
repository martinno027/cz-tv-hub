package cz.cztvhub.core

/** One digit typed into the channel number buffer: the new buffer and whether to use it now. */
data class DigitResult(val buffer: String, val commit: Boolean)

/**
 * Channel numbers and zapping (spec/apps.md "Channel numbers and zapping"): a channel's number is its 1-based
 * position in "Kanály" `visible`. Golden cases in spec/fixtures/channel_numbers_cases.json.
 */
object ChannelNumbers {
    /** Without a digit for this long the buffer is used. */
    const val ENTRY_TIMEOUT_MS = 2_000L

    /** The new stream is resolved only after this long without another zap press. */
    const val ZAP_DELAY_MS = 600L

    /** How long the channel overlay stays. */
    const val OVERLAY_MS = 3_000L

    private const val MAX_DIGITS = 3

    /** The channel number of [key], or null when it is not shown. */
    fun number(visible: List<String>, key: String): Int? = visible.indexOf(key).takeIf { it >= 0 }?.plus(1)

    /**
     * The neighbour of [current] in [visible] ([by] = −1 previous, +1 next), wrapping at both ends. A channel
     * that is not in the list goes to the first (next) or the last (previous); an empty list gives null.
     */
    fun step(visible: List<String>, current: String?, by: Int): String? {
        if (visible.isEmpty()) return null
        val index = current?.let(visible::indexOf) ?: -1
        if (index < 0) return if (by > 0) visible.first() else visible.last()
        return visible[Math.floorMod(index + by, visible.size)]
    }

    /**
     * Adds [digit] to [buffer] for a list of [count] channels: a leading 0 is ignored; the buffer is used at once
     * when no further digit could give a valid number or it has 3 digits.
     */
    fun type(buffer: String, digit: Int, count: Int): DigitResult {
        if (buffer.isEmpty() && digit == 0) return DigitResult("", false)
        val next = buffer + digit
        val number = next.toInt()
        return DigitResult(next, number.toLong() * 10 > count || next.length >= MAX_DIGITS)
    }

    /** The channel with [number] (1-based), or null when there is none. */
    fun resolve(visible: List<String>, number: Int): String? = visible.getOrNull(number - 1)
}
