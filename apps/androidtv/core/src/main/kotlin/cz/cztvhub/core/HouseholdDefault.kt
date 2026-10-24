package cz.cztvhub.core

/** `GET /dvb/defaults`: the household's channel list ([channelPrefs] null = none, the built-in order) and its version. */
data class HouseholdDefaults(val channelPrefs: ChannelPrefs?, val version: Long)

/**
 * The household default on a TV that is not signed in (spec/apps.md "Kanály" → "Household default (T11)"):
 * [own] = the viewer changed the list here, [local] = the TV's own list, [default] = the cached household default
 * (null = none) with its [version] (0 = never seen). [S] is the list type (labels in the golden cases).
 */
data class DefaultState<out S>(val own: Boolean, val local: S, val default: S?, val version: Long) {
    /** What Živě and the guide show: the TV's own list, else the household default, else [builtin]. */
    fun effective(builtin: @UnsafeVariance S): S = if (own) local else default ?: builtin
}

sealed interface DefaultEvent<out S> {
    /** `GET /dvb/defaults` answered; applied only when [version] is newer. */
    data class Defaults<S>(val settings: S?, val version: Long) : DefaultEvent<S>

    /** The viewer changed the list (hide/show, move, a source switch); [settings] = the new list. */
    data class Change<S>(val settings: S) : DefaultEvent<S>

    /** "Obnovit výchozí". */
    data object Reset : DefaultEvent<Nothing>

    /** The gateway was removed or replaced: no cached default, version 0. */
    data object Drop : DefaultEvent<Nothing>
}

/** The reducer, the same in all three apps (golden cases in spec/fixtures/household_default_cases.json). */
object HouseholdDefault {
    /** [empty] = the TV's list after a reset (no order, nothing hidden, every source on). */
    fun <S> reduce(state: DefaultState<S>, event: DefaultEvent<S>, empty: S): DefaultState<S> = when (event) {
        is DefaultEvent.Defaults ->
            if (event.version > state.version) state.copy(default = event.settings, version = event.version) else state
        is DefaultEvent.Change -> state.copy(own = true, local = event.settings)
        DefaultEvent.Reset -> state.copy(own = false, local = empty)
        DefaultEvent.Drop -> state.copy(default = null, version = 0)
    }

    /** A TV with prefs stored before T11 keeps them as its own (non-empty order, hidden or sources off). */
    fun isOwn(stored: ChannelPrefs): Boolean =
        stored.order.isNotEmpty() || stored.hidden.isNotEmpty() || stored.sourcesOff.isNotEmpty()
}

/**
 * The household default applied to this TV's "Kanály" store. Only while signed out: signed in, the profile's list
 * is shown and changes go to the profile (spec/apps.md "Profil"), but a newer default is still cached (and a reset
 * copies it into the profile). Main thread.
 *
 * [signedIn] = a profile is active; [shown] = the store's list; [show] puts a list into the store and returns
 * true when that changed it; [persist] keeps [state] across restarts.
 */
class HouseholdDefaultSync(
    initial: DefaultState<ChannelPrefs>,
    private val signedIn: () -> Boolean,
    private val shown: () -> ChannelPrefs,
    private val show: (ChannelPrefs) -> Boolean,
    private val persist: (DefaultState<ChannelPrefs>) -> Unit,
) {
    var state: DefaultState<ChannelPrefs> = initial
        private set

    /** A fetched default; returns true when the store changed. */
    fun defaults(defaults: HouseholdDefaults): Boolean {
        dispatch(DefaultEvent.Defaults(defaults.channelPrefs, defaults.version))
        return follow()
    }

    /** The viewer changed the store: signed out, the list is the TV's own from now on (starting from what it showed). */
    fun changed() {
        if (!signedIn()) dispatch(DefaultEvent.Change(shown()))
    }

    /**
     * "Obnovit výchozí". Signed out the TV follows the household default again (or the built-in order); signed in the
     * profile's list becomes a copy of it and the TV's own state is untouched. Returns true when the store changed
     * (signed in, the caller sends it to the profile like any change).
     */
    fun reset(): Boolean {
        if (signedIn()) return show(state.default ?: ChannelPrefs.DEFAULT)
        dispatch(DefaultEvent.Reset)
        return follow()
    }

    /**
     * "Odebrat bránu" or another gateway address: the cached default and its version are dropped (a different gateway
     * counts its versions from its own start). Returns true when the store changed.
     */
    fun dropCache(): Boolean {
        dispatch(DefaultEvent.Drop)
        return follow()
    }

    /**
     * Signed out and not [DefaultState.own]: the store shows the effective list (app start, after a sign-out, after a
     * fetch). Returns true when the store changed.
     */
    fun follow(): Boolean {
        if (signedIn() || state.own) return false
        return show(state.effective(ChannelPrefs.DEFAULT))
    }

    private fun dispatch(event: DefaultEvent<ChannelPrefs>) {
        val next = HouseholdDefault.reduce(state, event, ChannelPrefs.DEFAULT)
        if (next == state) return
        state = next
        persist(next)
    }
}
