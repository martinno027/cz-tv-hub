import CTCore
import SwiftUI

/// Full-screen notice for CT programmes offered only with DRM, which this app never requests.
/// The way out is CT's own iVysílání app.
struct DRMErrorView: View {
    let error: CTError
    let onBack: () -> Void
    let onOpenOfficialApp: () -> Void

    private enum Field { case open }
    @FocusState private var focus: Field?

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 36) {
                Image(systemName: "lock.fill")
                    .font(.system(size: 90))
                    .foregroundStyle(.secondary)
                Text(ctText)
                    .font(.title2.weight(.bold))
                    .multilineTextAlignment(.center)
                Text(CTError.drmHint)
                    .font(.headline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                HStack(spacing: 40) {
                    Button(OfficialApp.buttonTitle, action: onOpenOfficialApp)
                        .focused($focus, equals: .open)
                    Button("Zpět", action: onBack)
                }
                .padding(.top, 20)
            }
            .frame(maxWidth: 1200)
            .focusSection()
        }
        .defaultFocus($focus, .open)
        .onAppear { focus = .open }
        .onExitCommand(perform: onBack)
    }

    /// CT's own text without the hint the parser appends.
    private var ctText: String {
        let suffix = "\n" + CTError.drmHint
        let message = error.userMessage
        return message.hasSuffix(suffix) ? String(message.dropLast(suffix.count)) : message
    }
}
