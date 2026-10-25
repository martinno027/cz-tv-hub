package cz.cztvhub.tv

import android.content.Context
import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.profile.ProfileClient
import cz.cztvhub.core.profile.ProfileCoordinator
import cz.cztvhub.core.profile.ProfileHost
import cz.cztvhub.core.profile.ProfileLogin
import cz.cztvhub.core.profile.ProfileRunner
import cz.cztvhub.core.profile.ProfileSync
import cz.cztvhub.core.profile.ProfileSyncState
import cz.cztvhub.core.profile.SettingsDocument
import cz.cztvhub.tv.dvb.DvbSettings
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlin.coroutines.resume

/**
 * "Profil" (spec/apps.md) for the screens: [ProfileCoordinator] over this TV's stores, the encrypted login in
 * [DvbSettings] and the sync state in SharedPreferences `profile_sync` (the TV's own settings while signed in,
 * pending, version, the active document with fields this app does not know). Main thread; no password is kept.
 */
class ProfileManager(
    context: Context,
    private val dvbSettings: DvbSettings,
    private val channelPrefs: ChannelPrefsStore,
    private val thumbnails: ThumbnailPrefs,
    /** A client for the gateway as reached now (directly or over the Tailscale bridge). */
    private val client: () -> ProfileClient?,
) {
    private val prefs = context.getSharedPreferences("profile_sync", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    private val host: ProfileHost = object : ProfileHost {
        override fun login() = dvbSettings.profile.value
        override fun saveLogin(login: ProfileLogin?) = if (login == null) dvbSettings.clearProfile() else dvbSettings.saveProfile(login)
        override fun client() = this@ProfileManager.client()
        override fun currentPrefs(): ChannelPrefs = channelPrefs.value.value
        override fun currentThumbnails() = thumbnails.on.value

        override fun apply(settings: SettingsDocument) {
            channelPrefs.set(settings.channelPrefs)
            thumbnails.set(settings.thumbnails)
        }

        override fun persist(state: ProfileSyncState<SettingsDocument>) {
            prefs.edit()
                .putString(ACTIVE, state.active.encode())
                .putString(LOCAL, state.local?.encode())
                .putBoolean(PENDING, state.pending)
                .putLong(VERSION, state.version ?: -1)
                .apply()
        }

        override fun changed(state: ProfileSyncState<SettingsDocument>, saveFailed: Boolean) {
            _state.value = state
            _saveFailed.value = saveFailed
        }

        override fun say(text: String) {
            _message.value = text
        }
    }

    private val runner: ProfileRunner = object : ProfileRunner {
        override fun <T> io(work: () -> T, done: (Result<T>) -> Unit) {
            scope.launch { done(withContext(Dispatchers.IO) { runCatching(work) }) }
        }

        override fun later(ms: Long, action: () -> Unit): () -> Unit {
            val job = scope.launch {
                delay(ms)
                action()
            }
            return { job.cancel() }
        }
    }

    private val coordinator: ProfileCoordinator = ProfileCoordinator(host, runner, restore())

    private val _state: MutableStateFlow<ProfileSyncState<SettingsDocument>> = MutableStateFlow(coordinator.state)
    val state: StateFlow<ProfileSyncState<SettingsDocument>> = _state.asStateFlow()

    private val _saveFailed: MutableStateFlow<Boolean> = MutableStateFlow(coordinator.saveFailed)
    val saveFailed: StateFlow<Boolean> = _saveFailed.asStateFlow()

    /** Said once (the gateway ended the session); [consumeMessage] clears it. */
    private val _message: MutableStateFlow<String?> = MutableStateFlow(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    init {
        // The restored state may be the TV's own settings again (login lost): put them into the stores.
        host.apply(coordinator.state.active)
        host.persist(coordinator.state)
        scope.launch {
            while (true) {
                delay(ProfileSync.RETRY_MS)
                coordinator.retry()
            }
        }
    }

    fun consumeMessage() {
        _message.value = null
    }

    /** A store changed (Kanály, the thumbnails switch). */
    fun changed() = coordinator.changed()

    /** App start and Nastavení open. */
    fun sync() = coordinator.sync()

    /** Null on success, else the Czech text for the form. */
    suspend fun signIn(name: String, password: String): String? =
        suspendCancellableCoroutine { c -> coordinator.signIn(name, password) { c.resume(it) } }

    suspend fun create(name: String, password: String, again: String): String? =
        suspendCancellableCoroutine { c -> coordinator.create(name, password, again) { c.resume(it) } }

    suspend fun signOut() = suspendCancellableCoroutine { c -> coordinator.signOut { c.resume(Unit) } }

    /** "Odebrat bránu" while signed in. */
    fun signOutLocally() = coordinator.signOutLocally()

    private fun restore(): ProfileSyncState<SettingsDocument> = ProfileCoordinator.restore(
        dvbSettings.profile.value,
        SettingsDocument.decode(prefs.getString(ACTIVE, null)),
        prefs.getString(LOCAL, null)?.let(SettingsDocument::decode),
        prefs.getBoolean(PENDING, false),
        prefs.getLong(VERSION, -1).takeIf { it >= 0 },
        channelPrefs.value.value,
        thumbnails.on.value,
    )

    private companion object {
        const val ACTIVE = "active"
        const val LOCAL = "local"
        const val PENDING = "pending"
        const val VERSION = "version"
    }
}
