package cz.cztvhub.core

import java.util.Locale

/**
 * The long-OK list of the own-bar players (spec/apps.md "ČT player bar (T62)" "Audio and subtitles", also the
 * Anténa timeline player): the audio renditions (when there is a choice), then the subtitles — "Titulky: Vypnuto"
 * and the stream's subtitle renditions by their names; a stream without subtitles has no subtitle items.
 */
object TrackMenu {
    const val SUBTITLES_OFF = "Titulky: Vypnuto"
    const val SUBTITLES_PREFIX = "Titulky: "
    private val CZECH: Locale = Locale.forLanguageTag("cs")

    /** A track's name: its label, else its language in Czech ("Čeština"), else null (unknown). */
    fun trackName(label: String?, language: String?): String? {
        label?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        val tag = language?.trim()?.takeIf { it.isNotEmpty() && it != "und" } ?: return null
        return Locale.forLanguageTag(tag).getDisplayLanguage(CZECH)
            .takeIf { it.isNotBlank() && !it.equals(tag, ignoreCase = true) }
            ?.replaceFirstChar { it.titlecase(CZECH) }
    }

    /** A subtitle item's label: "Titulky: <name>", without a name "Titulky <n>" ([number] from 1). */
    fun subtitleLabel(name: String?, number: Int): String =
        name?.let { SUBTITLES_PREFIX + it } ?: "Titulky $number"

    /** The list's title: "Zvuk a titulky" when the stream has subtitles, else "Zvuk". */
    fun title(hasSubtitles: Boolean): String = if (hasSubtitles) "Zvuk a titulky" else "Zvuk"

    enum class Kind { AUDIO, SUBTITLES_OFF, SUBTITLE }

    /** One entry; [index] = the track among [Kind.AUDIO] / [Kind.SUBTITLE] tracks as passed in (−1 for off). */
    data class Item(val kind: Kind, val index: Int, val label: String, val selected: Boolean)

    /**
     * [audio] / [subtitles]: the tracks' names in order ([trackName]; a subtitle without one is "Titulky <n>"),
     * [selectedAudio] / [selectedSubtitle] the index playing (null = none; no subtitle = off). A single audio track
     * is no choice: left out. Empty = nothing to choose.
     */
    fun items(audio: List<String>, selectedAudio: Int?, subtitles: List<String?>, selectedSubtitle: Int?): List<Item> {
        val items = mutableListOf<Item>()
        if (audio.size >= 2) audio.forEachIndexed { i, name -> items += Item(Kind.AUDIO, i, name, i == selectedAudio) }
        if (subtitles.isNotEmpty()) {
            val chosen = selectedSubtitle?.takeIf { it in subtitles.indices }
            items += Item(Kind.SUBTITLES_OFF, -1, SUBTITLES_OFF, chosen == null)
            subtitles.forEachIndexed { i, name -> items += Item(Kind.SUBTITLE, i, subtitleLabel(name, i + 1), i == chosen) }
        }
        return items
    }
}
