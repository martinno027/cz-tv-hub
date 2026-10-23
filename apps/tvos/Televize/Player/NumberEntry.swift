import CTCore
import SwiftUI

/// Digits typed on a remote or keyboard that has them (spec/apps.md "Number keys"): collected in a buffer shown
/// top right, used at once when no further digit could give a valid number, else after 2 s.
@MainActor
@Observable
final class NumberEntry {
    private(set) var digits = ""
    /// "Kanál <n> není" (or a zap error), shown in the same place for a moment.
    private(set) var message: String?

    @ObservationIgnored private let onNumber: (Int) -> Void
    @ObservationIgnored private var timeout: Task<Void, Never>?
    @ObservationIgnored private var messageTask: Task<Void, Never>?

    /// `onNumber` gets the typed number once the buffer is used.
    init(onNumber: @escaping (Int) -> Void) {
        self.onNumber = onNumber
    }

    /// One digit; `count` = how many channels have a number.
    func type(_ digit: Int, count: Int) {
        let result = ChannelNumbers.type(buffer: digits, digit: digit, count: count)
        guard !result.buffer.isEmpty else { return }
        timeout?.cancel()
        message = nil
        digits = result.buffer
        if result.commit {
            finish()
        } else {
            timeout = Task { [weak self] in
                try? await Task.sleep(for: ChannelNumbers.entryTimeout)
                guard !Task.isCancelled else { return }
                self?.finish()
            }
        }
    }

    func show(missing number: Int) {
        show(message: ChannelNumbers.missingMessage(String(number)))
    }

    func show(message text: String) {
        message = text
        messageTask?.cancel()
        messageTask = Task { [weak self] in
            try? await Task.sleep(for: ChannelNumbers.entryTimeout)
            guard !Task.isCancelled else { return }
            self?.message = nil
        }
    }

    func cancel() {
        timeout?.cancel()
        messageTask?.cancel()
        digits = ""
        message = nil
    }

    private func finish() {
        timeout?.cancel()
        let number = Int(digits) ?? 0
        digits = ""
        onNumber(number)
    }

    /// The digit of a hardware key press (keyboard or a remote that sends digits), if it is one.
    static func digit(of press: UIPress) -> Int? {
        guard let characters = press.key?.charactersIgnoringModifiers, characters.count == 1 else { return nil }
        return Int(characters)
    }
}

/// The buffer (`2_`) or the "Kanál <n> není" message, top right.
struct NumberEntryBadge: View {
    let entry: NumberEntry

    var body: some View {
        if let text = entry.digits.isEmpty ? entry.message : entry.digits + "_" {
            Text(text)
                .font(.system(size: 56, weight: .bold).monospacedDigit())
                .padding(.horizontal, 32)
                .padding(.vertical, 16)
                .background(.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 16))
                .padding(60)
                .allowsHitTesting(false)
        }
    }
}
