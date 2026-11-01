package cz.cztvhub.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MuteTest {
    /** A player of the app: takes the session's volume when it starts, like PlayerActivity.onStart and a zap. */
    private class FakePlayer(session: MuteSession) {
        val volume = session.volume
    }

    @Test
    fun startsWithSound() {
        val session = MuteSession()
        assertFalse(session.muted)
        assertEquals(1f, session.volume)
    }

    @Test
    fun carriesToTheNextProgramme() {
        val session = MuteSession()
        val first = FakePlayer(session)
        assertTrue(session.toggle())
        // The next programme (and a zap, a timeline window) is a new player in the same app session.
        val next = FakePlayer(session)
        assertEquals(0f, next.volume)
        assertNotEquals(first.volume, next.volume)
        assertEquals(0f, FakePlayer(session).volume)
    }

    @Test
    fun unmuteCarriesToo() {
        val session = MuteSession()
        session.toggle()
        assertFalse(session.toggle())
        assertEquals(1f, FakePlayer(session).volume)
    }

    @Test
    fun aNewSessionHasSound() {
        MuteSession().toggle()
        // Not stored: the next app session starts unmuted.
        assertEquals(1f, FakePlayer(MuteSession()).volume)
    }
}
