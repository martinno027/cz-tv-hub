package cz.cztvhub.tv

import androidx.media3.common.C
import androidx.media3.common.Format
import androidx.media3.common.MimeTypes
import androidx.media3.common.TrackGroup
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.TrackSelectionParameters
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioPreferenceTest {
    private fun audio(id: String, language: String?, roleFlags: Int = 0): Format =
        Format.Builder().setId(id).setSampleMimeType(MimeTypes.AUDIO_AAC).setLanguage(language)
            .setRoleFlags(roleFlags).build()

    private fun withOverride(group: TrackGroup, index: Int): TrackSelectionParameters =
        TrackSelectionParameters.DEFAULT.buildUpon()
            .setOverrideForType(TrackSelectionOverride(group, index))
            .build()

    @Test
    fun defaultPrefersCzechMainAudio() {
        val params = AudioPreference.DEFAULT.applyTo(TrackSelectionParameters.DEFAULT)
        assertEquals(listOf("cs"), params.preferredAudioLanguages)
        assertEquals(C.ROLE_FLAG_MAIN, params.preferredAudioRoleFlags)
    }

    @Test
    fun noOverrideMeansNoChoice() {
        assertNull(AudioPreference.chosen(TrackSelectionParameters.DEFAULT))
    }

    @Test
    fun chosenTrackBecomesThePreference() {
        // HLS renditions: one track group each.
        val english = TrackGroup("en", audio("en", "en"))
        val described = TrackGroup("ad", audio("ad", "cs", C.ROLE_FLAG_DESCRIBES_VIDEO))
        assertEquals(AudioPreference("en", 0), AudioPreference.chosen(withOverride(english, 0)))
        assertEquals(
            AudioPreference("cs", C.ROLE_FLAG_DESCRIBES_VIDEO),
            AudioPreference.chosen(withOverride(described, 0)),
        )
    }

    @Test
    fun trackWithoutLanguageIsNotRemembered() {
        assertNull(AudioPreference.chosen(withOverride(TrackGroup("x", audio("x", null)), 0)))
        assertNull(AudioPreference.chosen(withOverride(TrackGroup("y", audio("y", C.LANGUAGE_UNDETERMINED)), 0)))
    }

    @Test
    fun applyingDropsStaleOverrides() {
        val params = AudioPreference("en", 0).applyTo(withOverride(TrackGroup("en", audio("en", "en")), 0))
        assertTrue(params.overrides.isEmpty())
        assertEquals(listOf("en"), params.preferredAudioLanguages)
        assertEquals(0, params.preferredAudioRoleFlags)
    }
}
