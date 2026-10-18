package cz.cztvhub.core

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.time.LocalDate
import java.time.format.DateTimeFormatter

data class HttpResponse(val status: Int, val body: String)

/** Seam for tests: the real implementation talks to CT, tests return fixtures. */
fun interface HttpTransport {
    /** POST when [jsonBody] is not null, GET otherwise. Throws [IOException] on network failure. */
    fun request(url: String, jsonBody: String?): HttpResponse
}

/**
 * Client for CT's public stream and programme APIs (the ones the iVysílání web player uses).
 * Blocking; call it off the main thread.
 */
class CtApi(private val transport: HttpTransport = UrlConnectionTransport()) {

    fun liveStreams(channelId: String): LiveStreams {
        val response = call("$API_BASE/video/v1/playlist-live/v1/stream-data/channel/$channelId?$STREAM_PARAMS", null)
        return CtParser.parseLive(channelId, response.status, response.body)
    }

    fun vodUrl(idec: String): String {
        val response = call("$API_BASE/video/v1/playlist-vod/v1/stream-data/media/external/$idec?$STREAM_PARAMS", null)
        return CtParser.parseVod(idec, response.status, response.body)
    }

    fun schedules(slugs: List<String>, day: LocalDate): Map<String, List<Programme>> {
        val variables = JsonObject(
            mapOf(
                "channels" to JsonArray(slugs.map(::JsonPrimitive)),
                "date" to JsonPrimitive(day.format(GRAPHQL_DATE)),
            ),
        )
        val body = JsonObject(mapOf("query" to JsonPrimitive(SCHEDULE_QUERY), "variables" to variables))
        val response = call(GRAPHQL_URL, body.toString())
        return CtParser.parseSchedule(response.status, response.body)
    }

    private fun call(url: String, jsonBody: String?): HttpResponse =
        try {
            transport.request(url, jsonBody)
        } catch (e: IOException) {
            throw CtException("request to ${url.substringBefore('?')} failed: $e")
        }

    companion object {
        const val API_BASE = "https://api.ceskatelevize.cz"
        const val STREAM_PARAMS = "canPlayDrm=false&quality=web&streamType=hls&origin=ivysilaniweb" +
            "&usePlayability=true&clientVersion=0.0.0"
        const val GRAPHQL_URL = "$API_BASE/graphql/?client=iVysilaniWeb&version=1.131.1&use-new-playability=true"
        const val USER_AGENT = "Mozilla/5.0 (compatible; cz-tv-hub/0.1)"
        private val GRAPHQL_DATE: DateTimeFormatter = DateTimeFormatter.ofPattern("MM.dd.yyyy")
        val SCHEDULE_QUERY = """
            query(${'$'}channels: [String!]!, ${'$'}date: Date!) {
              TVProgramDailyChannelsPlanV2(channels: ${'$'}channels, date: ${'$'}date) {
                channel
                program { idec title episodeTitle description genre imageUrl start end isPlayableNow liveOnly }
              }
            }
        """.trimIndent()
    }
}

class UrlConnectionTransport(private val timeoutMs: Int = 10_000) : HttpTransport {
    override fun request(url: String, jsonBody: String?): HttpResponse {
        val connection = URI(url).toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.setRequestProperty("User-Agent", CtApi.USER_AGENT)
            connection.setRequestProperty("Accept", "application/json")
            if (jsonBody != null) {
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(jsonBody.toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            val stream = if (status >= 400) connection.errorStream else connection.inputStream
            val body = stream?.use { it.readBytes().toString(Charsets.UTF_8) }.orEmpty()
            return HttpResponse(status, body)
        } finally {
            connection.disconnect()
        }
    }
}
