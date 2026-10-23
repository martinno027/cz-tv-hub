package cz.cztvhub.tv

import androidx.media3.common.C
import androidx.media3.common.TrackSelectionParameters

/**
 * Audio track the player prefers (spec/apps.md "Audio choice for Anténa"): Czech main audio by default; once the
 * viewer picks another track in the player's settings menu, its language and roles, so the choice survives a
 * re-resolved stream (new playlist = new track groups, the menu's override no longer applies) and a return
 * from the background.
 */
data class AudioPreference(val language: String, val roleFlags: Int) {

    /** [params] with this preference and without stale audio overrides. */
    fun applyTo(params: TrackSelectionParameters): TrackSelectionParameters =
        params.buildUpon()
            .clearOverridesOfType(C.TRACK_TYPE_AUDIO)
            .setPreferredAudioLanguage(language)
            .setPreferredAudioRoleFlags(roleFlags)
            .build()

    companion object {
        val DEFAULT = AudioPreference("cs", C.ROLE_FLAG_MAIN)

        /** The track the viewer picked in the menu (an audio override), or null when none or it has no language. */
        fun chosen(params: TrackSelectionParameters): AudioPreference? {
            val override = params.overrides.values
                .firstOrNull { it.type == C.TRACK_TYPE_AUDIO && it.trackIndices.isNotEmpty() } ?: return null
            val format = override.mediaTrackGroup.getFormat(override.trackIndices[0])
            val language = format.language?.takeIf { it.isNotBlank() && it != C.LANGUAGE_UNDETERMINED } ?: return null
            return AudioPreference(language, format.roleFlags)
        }
    }
}
