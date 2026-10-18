package cz.cztvhub.core.dvb

import cz.cztvhub.core.CtException
import cz.cztvhub.core.HttpResponse
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.time.LocalDate

/** Seam for tests: GET with headers. Throws [IOException] on network failure. */
fun interface DvbTransport {
    fun get(url: String, headers: Map<String, String>): HttpResponse
}

/** Client for the household gateway's `/dvb/…` JSON API. Blocking; call it off the main thread. */
class DvbClient(val config: DvbConfig, private val transport: DvbTransport = UrlConnectionDvbTransport()) {

    fun status(): DvbStatus = call("/dvb/status") { s, b -> DvbParser.parseStatus(s, b) }

    fun channels(): List<DvbChannel> = call("/dvb/channels") { s, b -> DvbParser.parseChannels(s, b) }

    /** Events of a Prague broadcast day (05:00–05:00). */
    fun epg(day: LocalDate): List<DvbEvent> = call("/dvb/epg?date=$day") { s, b -> DvbParser.parseEpg(s, b) }

    fun liveUrl(channelId: String): String = config.url("/dvb/live/$channelId.m3u8")

    fun recordingUrl(recordingId: String): String = config.url("/dvb/recording/$recordingId.m3u8")

    private fun <T> call(path: String, parse: (Int, String) -> T): T {
        val response = try {
            transport.get(config.url(path), config.authHeaders + ("Accept" to "application/json"))
        } catch (e: IOException) {
            throw CtException("gateway $path failed: $e", DvbMessages.UNREACHABLE)
        }
        return parse(response.status, response.body)
    }
}

class UrlConnectionDvbTransport(private val timeoutMs: Int = 8_000) : DvbTransport {
    override fun get(url: String, headers: Map<String, String>): HttpResponse {
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            headers.forEach { (k, v) -> connection.setRequestProperty(k, v) }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            return HttpResponse(status, stream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty())
        } finally {
            connection.disconnect()
        }
    }
}
