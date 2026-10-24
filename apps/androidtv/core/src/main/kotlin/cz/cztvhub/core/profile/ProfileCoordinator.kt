package cz.cztvhub.core.profile

import cz.cztvhub.core.ChannelPrefs
import cz.cztvhub.core.dvb.DvbMessages

/** A profile this TV is signed in to: its name and session token (never the password). */
data class ProfileLogin(val name: String, val token: String)

/** What the coordinator needs from the app: secure storage, the settings stores and the gateway. Main thread. */
interface ProfileHost {
    /** The login in the platform's secure storage. */
    fun login(): ProfileLogin?

    /** Stores the login; null deletes it. */
    fun saveLogin(login: ProfileLogin?)

    /** A client for the configured gateway, or null without one. */
    fun client(): ProfileClient?

    /** The stores' current values (Kanály, the thumbnails switch). */
    fun currentPrefs(): ChannelPrefs
    fun currentThumbnails(): Boolean

    /** Puts [settings] into the stores (no change notification back). */
    fun apply(settings: SettingsDocument)

    /** Keeps the sync state across restarts (the TV's own settings, pending, version, the active document). */
    fun persist(state: ProfileSyncState<SettingsDocument>)

    /** Something the screens show changed. */
    fun changed(state: ProfileSyncState<SettingsDocument>, saveFailed: Boolean)

    /** A message to show once ([ProfileSync.SIGNED_OUT_TEXT]). */
    fun say(text: String)
}

/** Threads and timers, so tests run synchronously. */
interface ProfileRunner {
    /** Runs blocking [work] off the main thread, then [done] on the main thread. */
    fun <T> io(work: () -> T, done: (Result<T>) -> Unit)

    /** Runs [action] on the main thread after [ms]; the returned function cancels it. */
    fun later(ms: Long, action: () -> Unit): () -> Unit
}

/**
 * "Profil" sync on a TV (spec/apps.md): the stores always hold the active settings; every state change goes
 * through [ProfileSync.reduce]. A change while signed in is sent after [ProfileSync.PUSH_DEBOUNCE_MS] of quiet; a
 * failed send stays pending and is retried by [sync] (app start, Nastavení open) and [retry] (every 60 s).
 * Only a `401` with the gateway's `profileSignedOut` code ends the session on its own.
 */
class ProfileCoordinator(
    private val host: ProfileHost,
    private val runner: ProfileRunner,
    initial: ProfileSyncState<SettingsDocument>,
) {
    var state: ProfileSyncState<SettingsDocument> = initial
        private set

    /** The last send failed ("Čeká na uložení — brána není dostupná"); a change pending since before a restart counts. */
    var saveFailed: Boolean = initial.pending
        private set

    private var cancelPush: (() -> Unit)? = null
    private var pushing = false
    private var changes = 0L

    /** A store changed: applied already; saved in the profile after the debounce. */
    fun changed() {
        changes++
        dispatch(ProfileEvent.Change(current()))
        if (!state.signedIn) return
        cancelPush?.invoke()
        cancelPush = runner.later(ProfileSync.PUSH_DEBOUNCE_MS) {
            cancelPush = null
            push()
        }
    }

    /** App start and Nastavení open: a pending change is sent first, else a newer profile version is applied. */
    fun sync() {
        val login = host.login() ?: return
        if (state.pending) return push()
        val client = host.client() ?: return
        runner.io({ client.get(login.token) }) { result ->
            if (host.login() != login) return@io
            result.onSuccess { dispatch(ProfileEvent.Remote(it.settings, it.version)) }
            result.onFailure { if (it.isSignedOut()) unauthorized() }
        }
    }

    /** The 60 s retry of a failed send. */
    fun retry() {
        if (state.pending && cancelPush == null) push()
    }

    /**
     * "Přihlásit": [done] gets null on success, else the Czech text for the form. Name and password are checked here
     * first (spec/apps.md "Profil"); the password is sent as typed (the gateway normalises it).
     */
    fun signIn(name: String, password: String, done: (String?) -> Unit) {
        val normalized = ProfileRules.normalizeName(name) ?: return done(BAD_NAME)
        if (!ProfileRules.isValidPassword(password)) return done(BAD_PASSWORD)
        val client = host.client() ?: return done(NEEDS_GATEWAY)
        runner.io({ client.login(normalized, password) }) { result ->
            val session = result.getOrElse { return@io done(it.formText()) }
            cancelPush?.invoke()
            cancelPush = null
            host.saveLogin(ProfileLogin(session.name, session.token))
            saveFailed = false
            dispatch(ProfileEvent.SignIn(session.name, session.settings, session.version))
            done(null)
        }
    }

    /** "Vytvořit": the new profile starts with this TV's current settings. */
    fun create(name: String, password: String, again: String, done: (String?) -> Unit) {
        val normalized = ProfileRules.normalizeName(name) ?: return done(BAD_NAME)
        if (!ProfileRules.isValidPassword(password)) return done(BAD_PASSWORD)
        if (password != again) return done(PASSWORDS_DIFFER)
        val client = host.client() ?: return done(NEEDS_GATEWAY)
        val current = current()
        runner.io({ client.create(normalized, password, current) }) { result ->
            val session = result.getOrElse { return@io done(it.formText()) }
            host.saveLogin(ProfileLogin(session.name, session.token))
            saveFailed = false
            dispatch(ProfileEvent.Create(session.name, session.version))
            done(null)
        }
    }

    /** "Odhlásit": ends the session on the gateway (failure ignored), then the TV's own settings apply again. */
    fun signOut(done: () -> Unit) {
        val login = host.login() ?: return done()
        val client = host.client() ?: return endSession().also { done() }
        runner.io({ client.logout(login.token) }) {
            if (host.login() == login) endSession()
            done()
        }
    }

    /** "Odebrat bránu": signed out on this TV at once; the gateway is still told if it answers (not waited for). */
    fun signOutLocally() {
        val login = host.login() ?: return
        host.client()?.let { client -> runner.io({ client.logout(login.token) }) {} }
        endSession()
    }

    private fun push() {
        val login = host.login() ?: return
        if (pushing || !state.pending) return
        val client = host.client() ?: return failed()
        pushing = true
        val serial = changes
        val settings = state.active // read here: the work runs on another thread
        runner.io({ client.put(login.token, settings) }) { result ->
            pushing = false
            if (host.login() != login) return@io
            result.onSuccess { version ->
                saveFailed = false
                dispatch(ProfileEvent.Pushed(version))
                // A change made while the send was under way is still to be saved.
                if (serial != changes) changed()
            }
            result.onFailure { if (it.isSignedOut()) unauthorized() else failed() }
        }
    }

    private fun failed() {
        saveFailed = true
        dispatch(ProfileEvent.PushFailed)
    }

    private fun unauthorized() {
        cancelPush?.invoke()
        cancelPush = null
        val step = ProfileSync.reduce(state, ProfileEvent.Unauthorized)
        host.saveLogin(null)
        saveFailed = false
        commit(step.state)
        if (step.message == ProfileMessage.SIGNED_OUT) host.say(ProfileSync.SIGNED_OUT_TEXT)
    }

    private fun endSession() {
        cancelPush?.invoke()
        cancelPush = null
        host.saveLogin(null)
        saveFailed = false
        dispatch(ProfileEvent.SignOut)
    }

    /** The active document with the stores' values (the truth for the fields this app knows). */
    private fun current(): SettingsDocument = state.active.with(host.currentPrefs(), host.currentThumbnails())

    private fun dispatch(event: ProfileEvent<SettingsDocument>) = commit(ProfileSync.reduce(state, event).state)

    private fun commit(next: ProfileSyncState<SettingsDocument>) {
        state = next
        host.apply(next.active)
        host.persist(next)
        host.changed(next, saveFailed)
    }

    private fun Throwable.isSignedOut(): Boolean = this is ProfileException && signedOut

    private fun Throwable.formText(): String = (this as? ProfileException)?.userMessage ?: DvbMessages.GENERIC

    companion object {
        const val NEEDS_GATEWAY = "Profily se ukládají na bránu Raspberry. Nejdřív ji nastavte v Nastavení → Server."
        const val PASSWORDS_DIFFER = "Hesla se neshodují"
        const val BAD_NAME = "Jméno musí mít 1 až 32 znaků."
        const val BAD_PASSWORD = "Heslo musí mít 4 až 64 znaků."

        /**
         * The state after a restart from what was stored. Without a login (signed out, or the secure storage lost
         * it) the TV's own settings, if kept aside, apply again. The stores' values win for the known fields.
         */
        fun restore(
            login: ProfileLogin?,
            active: SettingsDocument,
            local: SettingsDocument?,
            pending: Boolean,
            version: Long?,
            prefs: ChannelPrefs,
            thumbnails: Boolean,
        ): ProfileSyncState<SettingsDocument> {
            val current = active.with(prefs, thumbnails)
            if (login == null) return ProfileSyncState.signedOut(local ?: current)
            return ProfileSyncState(login.name, current, local ?: current, pending, version)
        }
    }
}
