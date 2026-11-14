package cz.cztvhub.core

import cz.cztvhub.core.TrackMenu.Kind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** spec/apps.md "ČT player bar (T62)" "Audio and subtitles": the long-OK list. */
class TrackMenuTest {
    @Test fun audioThenSubtitlesOffThenTheSubtitlesByName() {
        val items = TrackMenu.items(listOf("Čeština", "Původní"), 0, listOf("Čeština", "Čeština (skryté)"), null)
        assertEquals(
            listOf("Čeština", "Původní", "Titulky: Vypnuto", "Titulky: Čeština", "Titulky: Čeština (skryté)"),
            items.map { it.label },
        )
        assertEquals(listOf(Kind.AUDIO, Kind.AUDIO, Kind.SUBTITLES_OFF, Kind.SUBTITLE, Kind.SUBTITLE), items.map { it.kind })
        assertEquals("audio 0 and subtitles off are on", listOf(true, false, true, false, false), items.map { it.selected })
        assertEquals(listOf(0, 1, -1, 0, 1), items.map { it.index })
    }

    @Test fun aChosenSubtitleIsMarkedInsteadOfOff() {
        val items = TrackMenu.items(listOf("Čeština"), 0, listOf("Čeština"), 0)
        assertEquals(listOf(false, true), items.map { it.selected })
    }

    @Test fun noSubtitlesInTheStreamMeansNoSubtitleItems() {
        assertTrue(TrackMenu.items(listOf("Čeština", "Původní"), 1, emptyList(), null).none { it.kind != Kind.AUDIO })
    }

    @Test fun aSingleAudioTrackIsNoChoice() {
        assertEquals(listOf(Kind.SUBTITLES_OFF, Kind.SUBTITLE), TrackMenu.items(listOf("Čeština"), 0, listOf("CZ"), null).map { it.kind })
        assertTrue("nothing to choose", TrackMenu.items(listOf("Čeština"), 0, emptyList(), null).isEmpty())
    }
}
