package cz.cztvhub.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** Reads `spec/fixtures/<name>` (wired in as test resources). */
object Fixtures {
    fun text(name: String): String =
        requireNotNull(Fixtures::class.java.classLoader.getResource(name)) { "missing fixture $name" }
            .readText()

    fun json(name: String): JsonObject = Json.parseToJsonElement(text(name)).jsonObject

    /** Fixture files wrap responses as `{"status": …, "body": …}`. */
    fun response(name: String): HttpResponse {
        val root = json(name)
        return HttpResponse(root.getValue("status").jsonPrimitive.int, root.getValue("body").toString())
    }
}

/** Serves fixtures by URL substring and records every request. */
class FakeTransport(private val routes: Map<String, String>) : HttpTransport {
    val requests = mutableListOf<Pair<String, String?>>()

    override fun request(url: String, jsonBody: String?): HttpResponse {
        requests += url to jsonBody
        val fixture = routes.entries.firstOrNull { url.contains(it.key) }?.value
            ?: throw java.io.IOException("no route for $url")
        return Fixtures.response(fixture)
    }
}
