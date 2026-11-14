package cz.cztvhub.tv

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.os.CancellationSignal
import androidx.core.content.ContextCompat
import androidx.core.location.LocationManagerCompat
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/**
 * This TV's coarse location for Domů's weather (T59, spec/apps.md "Location for weather"): the network provider only
 * (no Google Play Services; many Android TVs have none, then there is no location). Kept by nobody: the caller rounds
 * it and sends it with one request.
 */
object DeviceLocation {
    const val PERMISSION = Manifest.permission.ACCESS_COARSE_LOCATION

    /** A fresh fix may take this long; then the last known one (or none). */
    private const val TIMEOUT_MS = 5_000L

    fun granted(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, PERMISSION) == PackageManager.PERMISSION_GRANTED

    /** Null without the permission, without a network provider (or with it off), or when nothing comes. */
    suspend fun current(context: Context): Location? {
        if (!granted(context)) return null
        val manager = context.getSystemService(LocationManager::class.java) ?: return null
        val provider = LocationManager.NETWORK_PROVIDER
        return try {
            if (provider !in manager.allProviders || !manager.isProviderEnabled(provider)) return null
            withTimeoutOrNull(TIMEOUT_MS) {
                suspendCancellableCoroutine { continuation ->
                    val signal = CancellationSignal()
                    continuation.invokeOnCancellation { signal.cancel() }
                    LocationManagerCompat.getCurrentLocation(manager, provider, signal, ContextCompat.getMainExecutor(context)) {
                        continuation.resume(it)
                    }
                }
            } ?: manager.getLastKnownLocation(provider)
        } catch (_: SecurityException) {
            null
        } catch (_: IllegalArgumentException) {
            null
        }
    }
}
