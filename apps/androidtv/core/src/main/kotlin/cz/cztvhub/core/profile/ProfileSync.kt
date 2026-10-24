package cz.cztvhub.core.profile

/**
 * Where this TV's settings live while signed in to a profile (spec/apps.md "Profil" → Sync). [active] = the settings
 * the TV uses, [local] = the TV's own settings kept aside while signed in, [pending] = a change not yet saved in
 * the profile, [version] = the profile version last seen. [S] is the settings type (labels in the golden cases).
 */
data class ProfileSyncState<out S>(
    val profile: String?,
    val active: S,
    val local: S?,
    val pending: Boolean,
    val version: Long?,
) {
    val signedIn: Boolean get() = profile != null

    companion object {
        fun <S> signedOut(active: S): ProfileSyncState<S> = ProfileSyncState(null, active, null, false, null)
    }
}

sealed interface ProfileEvent<out S> {
    /** Signed in: the profile's [settings] become active. */
    data class SignIn<S>(val name: String, val settings: S, val version: Long) : ProfileEvent<S>

    /** Created: the profile starts with the current settings. */
    data class Create(val name: String, val version: Long) : ProfileEvent<Nothing>

    /** The viewer changed a setting (Kanály, the thumbnails switch). */
    data class Change<S>(val settings: S) : ProfileEvent<S>

    /** `PUT /dvb/profile` succeeded. */
    data class Pushed(val version: Long) : ProfileEvent<Nothing>

    data object PushFailed : ProfileEvent<Nothing>

    /** `GET /dvb/profile` answered. */
    data class Remote<S>(val settings: S, val version: Long) : ProfileEvent<S>

    /** "Odhlásit". */
    data object SignOut : ProfileEvent<Nothing>

    /** A profile call answered `401` with `"code": "profileSignedOut"`. */
    data object Unauthorized : ProfileEvent<Nothing>
}

/** A message to show once after a step. */
enum class ProfileMessage { SIGNED_OUT }

data class ProfileStep<out S>(val state: ProfileSyncState<S>, val message: ProfileMessage? = null)

/** The sync reducer, the same in all three apps (golden cases in spec/fixtures/profile_cases.json "sync"). */
object ProfileSync {
    const val PUSH_DEBOUNCE_MS = 1_000L
    const val RETRY_MS = 60_000L

    /** The message the TV says once after [ProfileMessage.SIGNED_OUT]. */
    const val SIGNED_OUT_TEXT = "Profil byl odhlášen (smazaný nebo nové heslo). Platí zase nastavení této televize."

    fun <S> reduce(state: ProfileSyncState<S>, event: ProfileEvent<S>): ProfileStep<S> = when (event) {
        is ProfileEvent.SignIn ->
            ProfileStep(ProfileSyncState(event.name, event.settings, state.local ?: state.active, false, event.version))
        is ProfileEvent.Create ->
            ProfileStep(ProfileSyncState(event.name, state.active, state.local ?: state.active, false, event.version))
        is ProfileEvent.Change ->
            ProfileStep(state.copy(active = event.settings, pending = state.signedIn))
        is ProfileEvent.Pushed ->
            ProfileStep(if (state.signedIn) state.copy(pending = false, version = event.version) else state)
        ProfileEvent.PushFailed -> ProfileStep(state)
        // A pending change is never overwritten; the same version is what this TV already has.
        is ProfileEvent.Remote ->
            ProfileStep(
                if (!state.signedIn || state.pending || event.version == state.version) state
                else state.copy(active = event.settings, version = event.version),
            )
        ProfileEvent.SignOut -> ProfileStep(signOut(state))
        ProfileEvent.Unauthorized ->
            if (state.signedIn) ProfileStep(signOut(state), ProfileMessage.SIGNED_OUT) else ProfileStep(state)
    }

    private fun <S> signOut(state: ProfileSyncState<S>): ProfileSyncState<S> =
        if (state.signedIn) ProfileSyncState.signedOut(state.local ?: state.active) else state
}
