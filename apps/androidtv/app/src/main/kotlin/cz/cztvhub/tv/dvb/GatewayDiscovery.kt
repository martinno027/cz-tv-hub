package cz.cztvhub.tv.dvb

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow

/** A gateway announced on the LAN as `_cztvhub._tcp` (mDNS, LAN only; tailnet names are typed in). */
data class DiscoveredGateway(val name: String, val address: String)

object GatewayDiscovery {
    private const val SERVICE_TYPE = "_cztvhub._tcp."

    /** Emits the list of gateways found so far, until the collector stops. */
    @Suppress("DEPRECATION") // resolveService: the replacement needs API 34
    fun discover(context: Context): Flow<List<DiscoveredGateway>> = callbackFlow {
        val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
        val found = LinkedHashMap<String, DiscoveredGateway>()
        val listener = object : NsdManager.DiscoveryListener {
            override fun onServiceFound(info: NsdServiceInfo) {
                nsd.resolveService(info, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) = Unit
                    override fun onServiceResolved(info: NsdServiceInfo) {
                        val host = info.host?.hostAddress ?: return
                        val address = if (':' in host) "[$host]:${info.port}" else "$host:${info.port}"
                        found[info.serviceName] = DiscoveredGateway(info.serviceName, address)
                        trySend(found.values.toList())
                    }
                })
            }

            override fun onServiceLost(info: NsdServiceInfo) {
                found.remove(info.serviceName)
                trySend(found.values.toList())
            }

            override fun onDiscoveryStarted(serviceType: String) = Unit
            override fun onDiscoveryStopped(serviceType: String) = Unit
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) = Unit
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) = Unit
        }
        trySend(emptyList())
        nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener)
        awaitClose { runCatching { nsd.stopServiceDiscovery(listener) } }
    }
}
