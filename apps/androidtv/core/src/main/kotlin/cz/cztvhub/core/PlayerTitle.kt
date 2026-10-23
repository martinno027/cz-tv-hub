package cz.cztvhub.core

import java.time.Duration
import java.time.Instant

/** The two lines of the player's info bar (spec/apps.md "Title in the player's info bar"). */
data class InfoTitle(val title: String, val subtitle: String)

object PlayerTitle {
    /** Re-check live titles at the programme's end, at least every minute. */
    val MAX_CHECK: Duration = Duration.ofSeconds(60)
    private val MIN_CHECK: Duration = Duration.ofSeconds(1)

    /** Live: the programme airing now over the channel; the channel alone without guide data. */
    fun live(channelName: String, programme: String?, episode: String = ""): InfoTitle =
        if (programme.isNullOrBlank()) InfoTitle(channelName, "")
        else InfoTitle(programme, join(channelName, episode))

    /** Start-over, archive, recordings: the programme, the channel (and episode) below. */
    fun programme(title: String, channelName: String, episode: String = ""): InfoTitle =
        InfoTitle(title.ifBlank { channelName }, if (title.isBlank()) "" else join(channelName, episode))

    /** How long until the live title must be checked again. */
    fun nextCheck(now: Instant, currentEnd: Instant?): Duration {
        if (currentEnd == null) return MAX_CHECK
        val left = Duration.between(now, currentEnd)
        return when {
            left < MIN_CHECK -> MIN_CHECK
            left > MAX_CHECK -> MAX_CHECK
            else -> left
        }
    }

    private fun join(channel: String, episode: String) =
        listOf(channel, episode).filter { it.isNotBlank() }.joinToString(" · ")
}
