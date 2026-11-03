import Foundation

/// Rewind / fast-forward of the Anténa timeline player (spec/apps.md "Keys", T55; golden `trick` and `hold` in
/// spec/fixtures/timeline_cases.json). A ← / → click steps 10 s; a press held ≥ 0.5 s starts moving the playhead
/// in its direction at speed 1 (already while held); while it moves a press on the same side is speed + 1 (at most
/// 4), on the other side speed − 1, and from speed 1 the other side turns it round. OK / play-pause stop it at the
/// playhead, Back returns to where it started, anything else (▲ / ▼, a swipe) interrupts it.
///
/// Times are monotonic seconds (`CACurrentMediaTime()` in the app); positions are playlist seconds of the window.
/// The playhead's position is computed from the time, so it moves every frame, not in key-repeat steps.
public struct TrickPlay: Sendable, Equatable {
    /// Programme seconds per second of speed 1…4.
    public static let rates: [Double] = [60, 120, 180, 240]
    public static var maxSpeed: Int { rates.count }

    /// What the stop does with the player.
    public enum Stop: String, Sendable, Equatable {
        /// Seek exactly to the playhead and play (OK / play-pause, an edge without a neighbour).
        case playHere = "play-here"
        /// Seek back to where the movement started and play (Back).
        case `return`
        /// The caller takes over (▲ / ▼ jump, a swipe).
        case none
    }

    /// What an event asks the app to do.
    public enum Action: Sendable, Equatable {
        case none
        /// A click: seek this many seconds (signed) from the playhead, exactly; 0 = the click only showed the hidden
        /// bar (no seek).
        case step(Double)
        /// The playhead starts moving (the video pauses).
        case started
        /// Speed or direction changed while moving.
        case changed
        case stopped(Stop)
    }

    private struct Press: Sendable, Equatable {
        let sign: Int
        let began: Double
        let position: Double
        /// The bar was hidden at the key-down: a click only shows it, a hold moves as always.
        let barHidden: Bool
    }

    /// −1 back, 1 forward, 0 stopped.
    public private(set) var direction = 0
    /// 1…4 while moving, 0 stopped.
    public private(set) var speed = 0
    /// Where the last movement started (Back returns there), in the coordinates of the window it started in.
    public private(set) var origin: Double?
    /// A press that may still become a hold (stopped, key down, under 0.5 s).
    private var press: Press?
    private var anchorPosition: Double = 0
    private var anchorTime: Double = 0

    public init() {}

    public var isMoving: Bool { direction != 0 }

    /// Programme seconds per second while moving, 0 stopped.
    public var rate: Double { isMoving ? Self.rates[speed - 1] : 0 }

    /// The bar's sign next to the playhead's clock: `◀◀` / `▶▶`, from speed 2 on with the number (`◀◀ 2`).
    public var badge: String? {
        guard isMoving else { return nil }
        let arrows = direction < 0 ? "◀◀" : "▶▶"
        return speed > 1 ? "\(arrows) \(speed)" : arrows
    }

    /// The playhead at `time` while moving (nil while stopped).
    public func position(at time: Double) -> Double? {
        guard isMoving else { return nil }
        return anchorPosition + Double(direction) * rate * max(0, time - anchorTime)
    }

    // MARK: events

    /// ← (−1) / → (1) pressed with the playhead at `position`. While moving it changes the speed at once; stopped it
    /// waits for the release (a click) or for `update` to see it held 0.5 s (a hold). A key repeat of the same press
    /// (a press already down) is ignored. With the bar hidden (`barHidden`, the app shows it) a click is only that.
    public mutating func keyDown(_ sign: Int, position: Double, at time: Double, barHidden: Bool = false) -> Action {
        let sign = sign < 0 ? -1 : 1
        if isMoving { return change(sign, at: time) }
        guard press == nil else { return .none }
        press = Press(sign: sign, began: time, position: position, barHidden: barHidden)
        return .none
    }

    /// Called every frame (and before a release): a press held for `TimelineRules.holdThreshold` starts moving.
    public mutating func update(at time: Double) -> Action {
        guard !isMoving, let press, time - press.began >= TimelineRules.holdThreshold else { return .none }
        self.press = nil
        direction = press.sign
        speed = 1
        origin = press.position
        // Moves from the moment it became a hold, so the playhead does not jump by the half second held.
        anchorPosition = press.position
        anchorTime = press.began + TimelineRules.holdThreshold
        return .started
    }

    /// ← / → released: under 0.5 s a click (±10 s, or 0 when it only showed the bar), held longer the movement
    /// goes on (the hold adds nothing).
    public mutating func keyUp(_ sign: Int, at time: Double) -> Action {
        let sign = sign < 0 ? -1 : 1
        guard let press, press.sign == sign else { return .none }
        let action = update(at: time)
        if action != .none { return action }
        self.press = nil
        return .step(press.barHidden ? 0 : Double(sign) * TimelineRules.stepSeconds)
    }

    /// The system took the press (`pressesCancelled`): neither a click nor a hold.
    public mutating func cancelPress() { press = nil }

    /// ⏪ (−1) / ⏩ (1): stopped it starts moving at speed 1, moving it acts as a click on that side.
    public mutating func media(_ sign: Int, position: Double, at time: Double) -> Action {
        let sign = sign < 0 ? -1 : 1
        if isMoving { return change(sign, at: time) }
        press = nil
        direction = sign
        speed = 1
        origin = position
        anchorPosition = position
        anchorTime = time
        return .started
    }

    /// OK / play-pause: while moving, stop and play from the playhead.
    public mutating func ok() -> Action { isMoving ? stop(.playHere) : .none }

    /// Back: while moving, stop and return to where it started.
    public mutating func back() -> Action { isMoving ? stop(.return) : .none }

    /// ▲ / ▼, a swipe, an edge without a neighbour: stop; `stop` says what the player does.
    public mutating func interrupt(_ stop: Stop = .none) -> Action {
        press = nil
        return isMoving ? self.stop(stop) : .none
    }

    /// Goes on from `position` at `time` (a window switch moved the playhead to the new window's coordinates).
    public mutating func rebase(position: Double, at time: Double) {
        guard isMoving else { return }
        anchorPosition = position
        anchorTime = time
    }

    private mutating func change(_ sign: Int, at time: Double) -> Action {
        let here = position(at: time) ?? anchorPosition
        if sign == direction {
            speed = min(Self.maxSpeed, speed + 1)
        } else if speed > 1 {
            speed -= 1
        } else {
            direction = sign  // from speed 1 the other side turns it round
        }
        anchorPosition = here
        anchorTime = time
        return .changed
    }

    private mutating func stop(_ kind: Stop) -> Action {
        direction = 0
        speed = 0
        press = nil
        return .stopped(kind)
    }
}
