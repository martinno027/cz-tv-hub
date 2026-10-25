import CTCore
import SwiftUI

/// The current time top right, in the row of the tab bar (spec/apps.md "Clock (T22)"): Živě, the guide and Nastavení
/// with its full-screen sections, not the player or the programme page (both full-screen covers over it).
struct ClockView: View {
    /// Centre of the tab bar row, in points on the 1920 × 1080 screen. Horizontally the safe area still applies, so the
    /// clock ends where the content does ("Otevřít v iVysílání" in the guide).
    static let rowCenter: CGFloat = 80

    var body: some View {
        TimelineView(MinuteSchedule()) { context in
            Text(Clock.text(context.date))
                // as the tab bar's labels
                .font(.system(size: 25, weight: .semibold))
                .foregroundStyle(.primary)
        }
        .frame(height: 2 * Self.rowCenter)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// Fires when the view appears and then on every minute boundary (not every 60 s from the appearance).
private struct MinuteSchedule: TimelineSchedule {
    func entries(from startDate: Date, mode: TimelineScheduleMode) -> AnyIterator<Date> {
        var next: Date? = startDate
        return AnyIterator {
            defer { next = next.map(Clock.nextMinute(after:)) }
            return next
        }
    }
}

extension View {
    /// The clock over this screen, top right at the tab bar's height.
    func clockTopRight() -> some View {
        overlay(alignment: .topTrailing) { ClockView().ignoresSafeArea() }
    }
}
