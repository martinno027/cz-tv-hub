import Foundation
import Observation

/// A player whose own audio can be muted (AVPlayer in the app, a fake in the tests).
@MainActor
public protocol MutablePlayer: AnyObject {
    var isMuted: Bool { get set }
}

/// "Mute (T34)" (spec/apps.md): one mute state for every player until the app closes. Zapping, the next
/// programme, the timeline player and a new player from the guide or the archive all stay muted; the state is
/// never stored and not part of a profile (a new app start is unmuted).
@MainActor
@Observable
public final class PlayerMute {
    public private(set) var isMuted = false
    /// The player on screen; each new player is attached and takes the current state.
    @ObservationIgnored private weak var player: MutablePlayer?

    public init() {}

    /// The transport bar action for the current state.
    public var actionTitle: String { isMuted ? "Zapnout zvuk" : "Vypnout zvuk" }

    /// A new player (or the same one again): it plays with the session's state from its first frame.
    public func attach(_ player: MutablePlayer) {
        self.player = player
        player.isMuted = isMuted
    }

    /// A player that closed; a later one may already be attached and is kept.
    public func detach(_ player: MutablePlayer) {
        if self.player === player { self.player = nil }
    }

    public func toggle() {
        isMuted.toggle()
        player?.isMuted = isMuted
    }
}
