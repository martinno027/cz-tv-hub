package cz.cztvhub.core

/**
 * The long-OK list of the own-bar players (spec/apps.md "ČT player bar (T62)" "Audio and subtitles", also the
 * Anténa timeline player): the audio renditions (when there is a choice), then the subtitles — "Titulky: Vypnuto"
 * and the stream's subtitle renditions by their names; a stream without subtitles has no subtitle items.
 */
object TrackMenu {
    const val SUBTITLES_OFF = "Titulky: Vypnuto"
    const val SUBTITLES_PREFIX = "Titulky: "

    enum class Kind { AUDIO, SUBTITLES_OFF, SUBTITLE }

    /** One entry; [index] = the track among [Kind.AUDIO] / [Kind.SUBTITLE] tracks as passed in (−1 for off). */
    data class Item(val kind: Kind, val index: Int, val label: String, val selected: Boolean)

    /**
     * [audio] / [subtitles]: the tracks' names in order, [selectedAudio] / [selectedSubtitle] the index playing (null =
     * none; no subtitle = off). A single audio track is no choice: left out. Empty = nothing to choose.
     */
    fun items(audio: List<String>, selectedAudio: Int?, subtitles: List<String>, selectedSubtitle: Int?): List<Item> {
        val items = mutableListOf<Item>()
        if (audio.size >= 2) audio.forEachIndexed { i, name -> items += Item(Kind.AUDIO, i, name, i == selectedAudio) }
        if (subtitles.isNotEmpty()) {
            val chosen = selectedSubtitle?.takeIf { it in subtitles.indices }
            items += Item(Kind.SUBTITLES_OFF, -1, SUBTITLES_OFF, chosen == null)
            subtitles.forEachIndexed { i, name -> items += Item(Kind.SUBTITLE, i, SUBTITLES_PREFIX + name, i == chosen) }
        }
        return items
    }
}
