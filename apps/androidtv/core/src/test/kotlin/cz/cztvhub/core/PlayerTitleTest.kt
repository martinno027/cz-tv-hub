package cz.cztvhub.core

import java.time.Duration
import java.time.Instant
import org.junit.Test
import org.junit.Assert.assertEquals

class PlayerTitleTest {
    private val now = Instant.parse("2026-10-23T19:10:00Z")

    @Test
    fun liveShowsTheProgrammeOverTheChannel() {
        assertEquals(InfoTitle("Na tělo", "CT 1 PHA HD T2"), PlayerTitle.live("CT 1 PHA HD T2", "Na tělo"))
        assertEquals(InfoTitle("Události", "ČT1 · Díl 3"), PlayerTitle.live("ČT1", "Události", "Díl 3"))
        assertEquals(InfoTitle("ČT1", ""), PlayerTitle.live("ČT1", null))
        assertEquals(InfoTitle("ČT1", ""), PlayerTitle.live("ČT1", " "))
    }

    @Test
    fun programmes() {
        assertEquals(InfoTitle("Zprávy", "Nova HD"), PlayerTitle.programme("Zprávy", "Nova HD"))
        assertEquals(InfoTitle("Nova HD", ""), PlayerTitle.programme("", "Nova HD"))
    }

    @Test
    fun recheckAtTheEndAtLeastEveryMinute() {
        assertEquals(Duration.ofSeconds(20), PlayerTitle.nextCheck(now, now.plusSeconds(20)))
        assertEquals(Duration.ofSeconds(60), PlayerTitle.nextCheck(now, now.plusSeconds(3600)))
        assertEquals(Duration.ofSeconds(60), PlayerTitle.nextCheck(now, null))
        assertEquals(Duration.ofSeconds(1), PlayerTitle.nextCheck(now, now.minusSeconds(5)))
    }
}
