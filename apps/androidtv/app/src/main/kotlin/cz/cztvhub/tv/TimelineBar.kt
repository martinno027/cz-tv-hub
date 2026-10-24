package cz.cztvhub.tv

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import cz.cztvhub.core.PlayerTitle
import cz.cztvhub.core.dvb.DvbTimeline
import cz.cztvhub.core.dvb.TimelineRules
import java.time.Instant

/** What the timeline player shows at one moment; [position] is the playhead (or the seek being chosen). */
data class TimelineFrame(
    val timeline: DvbTimeline,
    /** The window's length in playlist seconds (a growing window: what the player already knows). */
    val duration: Double,
    val position: Double,
    val channelName: String,
    val now: Instant,
)

/**
 * The timeline player's own bar (spec/apps.md "Anténa timeline player"): the programme at the playhead over
 * "channel · episode", the wall clock above the playhead, the window as a bar in wall-clock time (recorded pieces
 * filled, gaps empty, programme ticks, the programme at the playhead highlighted, the played part) and its ends.
 */
class TimelineOverlay(context: Context) : LinearLayout(context) {
    private val title = text(34f, bold = true)
    private val subtitle = text(22f, alpha = 220)
    private val playheadLabel = text(24f, bold = true)
    private val noticeLine = text(22f).apply {
        setTextColor(Color.rgb(0xFF, 0xB4, 0x50))
        visibility = GONE
    }
    private var noticeUntil = 0L
    private val startLabel = text(20f, alpha = 200)
    private val endLabel = text(20f, alpha = 200)
    private val bar = TimelineBarView(context)
    private val labelRow = FrameLayout(context)

    init {
        orientation = VERTICAL
        setPadding(dp(48), dp(48), dp(48), dp(36))
        background = GradientDrawable(
            GradientDrawable.Orientation.BOTTOM_TOP,
            intArrayOf(Color.argb(220, 0, 0, 0), Color.TRANSPARENT),
        )
        addView(noticeLine)
        addView(title)
        addView(subtitle)
        labelRow.addView(playheadLabel, FrameLayout.LayoutParams(WRAP, WRAP))
        addView(labelRow, LayoutParams(MATCH, WRAP).apply { topMargin = dp(16) })
        addView(bar, LayoutParams(MATCH, dp(28)))
        val ends = FrameLayout(context)
        ends.addView(startLabel, FrameLayout.LayoutParams(WRAP, WRAP, Gravity.START))
        ends.addView(endLabel, FrameLayout.LayoutParams(WRAP, WRAP, Gravity.END))
        addView(ends, LayoutParams(MATCH, WRAP))
    }

    /** A short message in the bar (e.g. the gateway's reason a neighbouring window did not open), for [NOTICE_MS]. */
    fun notice(text: String) {
        noticeLine.text = text
        noticeLine.visibility = VISIBLE
        noticeUntil = android.os.SystemClock.elapsedRealtime() + NOTICE_MS
    }

    fun render(frame: TimelineFrame) {
        if (noticeLine.visibility == VISIBLE && android.os.SystemClock.elapsedRealtime() >= noticeUntil) {
            noticeLine.visibility = GONE
        }
        val timeline = frame.timeline
        val growing = !timeline.complete
        val pieces = timeline.pieces
        val windowStart = pieces.first().time
        val windowEnd = TimelineRules.toTime(pieces, frame.duration, growing)
        val playhead = TimelineRules.toTime(pieces, frame.position.coerceIn(0.0, frame.duration), growing)
        val index = TimelineRules.programmeAt(timeline.programmes, playhead)
        val programme = index?.let { timeline.programmes[it] }
        val info = PlayerTitle.programme(programme?.title.orEmpty(), frame.channelName, programme?.subtitle.orEmpty())
        title.text = info.title
        subtitle.text = info.subtitle
        subtitle.visibility = if (info.subtitle.isBlank()) GONE else VISIBLE
        playheadLabel.text = TimelineRules.label(playhead, frame.now)
        startLabel.text = TimelineRules.edgeLabel(windowStart, playhead, frame.now)
        endLabel.text = TimelineRules.endLabel(windowEnd, timeline.complete, playhead, frame.now)

        val startMs = windowStart.toEpochMilli()
        val lastPiece = pieces.last()
        bar.model = TimelineBarView.Model(
            startMs = startMs,
            endMs = windowEnd.toEpochMilli(),
            pieces = pieces.map { piece ->
                val end = if (growing && piece === lastPiece) windowEnd else piece.endTime
                piece.time.toEpochMilli() to end.toEpochMilli()
            },
            ticks = timeline.programmes.map { it.start.toEpochMilli() },
            highlight = programme?.let { it.start.toEpochMilli() to it.end.toEpochMilli() },
            playheadMs = playhead.toEpochMilli(),
        )
        // The wall clock sits above the playhead, kept inside the row.
        playheadLabel.post {
            val x = bar.xOf(playhead.toEpochMilli())
            val max = (labelRow.width - playheadLabel.width).toFloat().coerceAtLeast(0f)
            playheadLabel.translationX = (x - playheadLabel.width / 2f).coerceIn(0f, max)
        }
    }

    private fun text(sp: Float, bold: Boolean = false, alpha: Int = 255) = TextView(context).apply {
        setTextColor(Color.argb(alpha, 255, 255, 255))
        setTextSize(TypedValue.COMPLEX_UNIT_SP, sp)
        if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
        maxLines = 1
        ellipsize = TextUtils.TruncateAt.END
        setShadowLayer(8f, 0f, 2f, Color.BLACK)
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private companion object {
        const val MATCH = LayoutParams.MATCH_PARENT
        const val WRAP = LayoutParams.WRAP_CONTENT
        const val NOTICE_MS = 5_000L
    }
}

/** The bar itself, in wall-clock milliseconds from [Model.startMs] to [Model.endMs]. */
class TimelineBarView(context: Context) : View(context) {
    data class Model(
        val startMs: Long,
        val endMs: Long,
        val pieces: List<Pair<Long, Long>>,
        val ticks: List<Long>,
        val highlight: Pair<Long, Long>?,
        val playheadMs: Long,
    )

    var model: Model? = null
        set(value) {
            if (field == value) return
            field = value
            invalidate()
        }

    private val density = resources.displayMetrics.density
    private val window = paint(Color.argb(60, 255, 255, 255))
    private val recorded = paint(Color.argb(120, 255, 255, 255))
    private val highlighted = paint(Color.argb(210, 255, 255, 255))
    private val played = paint(Color.rgb(0x3D, 0x7B, 0xFD))
    private val tick = paint(Color.argb(230, 255, 255, 255))
    private val head = paint(Color.WHITE)
    private val rect = RectF()

    /** The x of a wall-clock moment on the bar. */
    fun xOf(ms: Long): Float {
        val m = model ?: return 0f
        val span = (m.endMs - m.startMs).coerceAtLeast(1)
        return width * ((ms - m.startMs).toFloat() / span).coerceIn(0f, 1f)
    }

    override fun onDraw(canvas: Canvas) {
        val m = model ?: return
        val mid = height / 2f
        val half = 4 * density
        // The window outline, so gaps read as empty stretches of it.
        rect.set(0f, mid - 1 * density, width.toFloat(), mid + 1 * density)
        canvas.drawRect(rect, window)
        for ((from, to) in m.pieces) {
            span(canvas, from, to, mid, half, recorded)
            m.highlight?.let { (a, b) -> span(canvas, maxOf(from, a), minOf(to, b), mid, half, highlighted) }
            span(canvas, from, minOf(to, m.playheadMs), mid, half, played)
        }
        for (t in m.ticks) {
            if (t <= m.startMs || t >= m.endMs) continue
            val x = xOf(t)
            rect.set(x - 1 * density, mid - 3 * half, x + 1 * density, mid + 3 * half)
            canvas.drawRect(rect, tick)
        }
        canvas.drawCircle(xOf(m.playheadMs), mid, 9 * density, head)
    }

    private fun span(canvas: Canvas, from: Long, to: Long, mid: Float, half: Float, paint: Paint) {
        if (to <= from) return
        rect.set(xOf(from), mid - half, xOf(to), mid + half)
        canvas.drawRect(rect, paint)
    }

    private fun paint(color: Int) = Paint(Paint.ANTI_ALIAS_FLAG).apply { this.color = color }
}
