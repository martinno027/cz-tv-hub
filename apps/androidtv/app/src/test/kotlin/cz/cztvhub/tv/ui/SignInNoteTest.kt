package cz.cztvhub.tv.ui

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** T37: the sign-in note is spec/apps.md "Sign-in note (T37)" word for word. */
class SignInNoteTest {
    private fun String.oneLine() = replace(Regex("\\s+"), " ")

    private val strings = File("src/main/res/values/strings.xml").readText()
    private val spec = generateSequence(File("").absoluteFile) { it.parentFile }
        .map { it.resolve("spec/apps.md") }.first { it.exists() }.readText().oneLine()

    @Test fun noteIsTheSpecText() {
        val note = Regex("<string name=\"signin_log_note\">([^<]*)</string>").find(strings)!!.groupValues[1]
        assertTrue(note, note.startsWith("Připojení a přihlášení se zaznamenává") && note.endsWith("se nezaznamenává."))
        val section = spec.substringAfter("**Sign-in note (T37)**").substringBefore("Golden cases")
        assertTrue(section, "„$note\"" in section || "„$note“" in section)
    }
}
