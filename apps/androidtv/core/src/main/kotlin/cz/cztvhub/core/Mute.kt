package cz.cztvhub.core

/**
 * Mute (spec/apps.md "Mute (T34)"): one state for every player of the app until the app is closed — zapping, the
 * next programme, a timeline window and a new player all start muted when the viewer muted. In memory only: never
 * stored, not in profiles; a new app process (a new session) starts with sound.
 */
class MuteSession {
    var muted: Boolean = false
        private set

    /** Flips the state; returns the new one. */
    fun toggle(): Boolean {
        muted = !muted
        return muted
    }

    /** The volume a player of this app gets (ExoPlayer `volume`): 0 muted, else full. */
    val volume: Float get() = if (muted) 0f else 1f
}
