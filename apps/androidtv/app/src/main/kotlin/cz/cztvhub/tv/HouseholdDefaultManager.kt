package cz.cztvhub.tv

import android.content.Context
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.CtException
import cz.cztvhub.core.DefaultState
import cz.cztvhub.core.HouseholdDefault
import cz.cztvhub.core.HouseholdDefaultSync
import cz.cztvhub.core.HouseholdDefaults
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The household default channel list (spec/apps.md "Kanály" → "Household default (T11)") over this TV's "Kanály"
 * store: `own` and the cached default with its version in SharedPreferences `household_default` (kept for offline
 * starts). While signed out and not `own` the store shows the default; [ProfileManager] is told of every store change
 * so a sign-in keeps what the TV showed. Main thread.
 */
class HouseholdDefaultManager(context: Context, private val app: TvApp) {
    private val prefs = context.getSharedPreferences("household_default", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val store = app.channelPrefs
    private val profiles = app.profiles

    private val sync = HouseholdDefaultSync(
        restore(),
        signedIn = { profiles.state.value.signedIn },
        shown = { store.value.value },
        show = store::set,
        persist = ::persist,
    )

    init {
        if (!prefs.contains(OWN)) persist(sync.state)
        // "Odebrat bránu" or another address (also while the app was not running): the cache belonged to the old one.
        scope.launch {
            app.dvbSettings.config.collect { config ->
                val address = config?.baseUrl
                if (address == prefs.getString(GATEWAY, null)) return@collect
                prefs.edit().putString(GATEWAY, address).apply()
                if (sync.dropCache()) profiles.changed()
            }
        }
        // App start, and every sign-out (also the gateway's "signed out"): back to following the default.
        scope.launch { profiles.state.collect { if (!it.signedIn) follow() } }
    }

    /** App start, a new gateway and Nastavení open; any failure keeps the cached default. */
    fun refresh() {
        val client = app.dvb()?.client ?: return
        scope.launch {
            val defaults = withContext(Dispatchers.IO) {
                try {
                    client.defaults()
                } catch (_: CtException) {
                    null // an older gateway answers 404; offline keeps the cache
                }
            } ?: return@launch
            if (app.dvb()?.client?.config != client.config) return@launch
            if (sync.defaults(defaults)) profiles.changed()
        }
    }

    /** The viewer changed "Kanály" (already in the store). */
    fun changed() = sync.changed()

    /**
     * "Obnovit výchozí": signed out the TV follows the household default again; signed in the profile's list becomes
     * a copy of it (or the built-in order). True when the store changed.
     */
    fun reset(): Boolean = sync.reset()

    /** Signed out and not the TV's own list: show the effective list (also done after every sign-out). */
    fun follow() {
        if (sync.follow()) profiles.changed()
    }

    private fun restore(): DefaultState<ChannelPrefs> {
        // Signed in, the store holds the profile's list; the TV's own is kept aside by the profile sync.
        val tvOwn = profiles.state.value.local?.channelPrefs ?: store.value.value
        val own = if (prefs.contains(OWN)) prefs.getBoolean(OWN, false) else HouseholdDefault.isOwn(tvOwn)
        return DefaultState(
            own,
            if (own) tvOwn else ChannelPrefs.DEFAULT,
            prefs.getString(DEFAULT, null)?.let(ChannelPrefs::decode),
            prefs.getLong(VERSION, 0),
        )
    }

    private fun persist(state: DefaultState<ChannelPrefs>) {
        prefs.edit()
            .putBoolean(OWN, state.own)
            .putString(DEFAULT, state.default?.encode())
            .putLong(VERSION, state.version)
            .apply()
    }

    private companion object {
        const val OWN = "own"
        const val DEFAULT = "default"
        const val VERSION = "version"

        /** The gateway address the cache came from. */
        const val GATEWAY = "gateway"
    }
}
