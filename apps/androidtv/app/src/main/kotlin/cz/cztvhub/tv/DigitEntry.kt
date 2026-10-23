package cz.cztvhub.tv

import cz.cztvhub.core.ChannelNumbers
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Number keys picking a channel (spec/apps.md "Channel numbers and zapping"): digits collect in a buffer that is
 * used at once when no further digit could give a valid number, else after 2 s without a digit or on OK.
 * [onBuffer] shows the buffer ("" = hide it), [onNumber] gets the number typed.
 */
class DigitEntry(
    private val scope: CoroutineScope,
    private val count: () -> Int,
    private val onBuffer: (String) -> Unit,
    private val onNumber: (Int) -> Unit,
) {
    private var buffer = ""
    private var timeout: Job? = null

    val isActive: Boolean get() = buffer.isNotEmpty()

    fun type(digit: Int) {
        val result = ChannelNumbers.type(buffer, digit, count())
        buffer = result.buffer
        if (result.commit) return commit()
        if (buffer.isEmpty()) return
        onBuffer(buffer)
        timeout?.cancel()
        timeout = scope.launch {
            delay(ChannelNumbers.ENTRY_TIMEOUT_MS)
            commit()
        }
    }

    /** Uses the buffer now (OK, the timeout or a final digit). */
    fun commit() {
        timeout?.cancel()
        timeout = null
        val typed = buffer
        buffer = ""
        onBuffer("")
        if (typed.isNotEmpty()) onNumber(typed.toInt())
    }

    fun cancel() {
        timeout?.cancel()
        timeout = null
        buffer = ""
        onBuffer("")
    }
}
