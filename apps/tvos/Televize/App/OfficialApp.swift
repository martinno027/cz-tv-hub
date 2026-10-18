import os
import UIKit

/// Opens the App Store page of Česká televize's official iVysílání app, which can play
/// programmes this app cannot (DRM-only). No deep links into the app itself.
@MainActor
enum OfficialApp {
    static let buttonTitle = "Otevřít v iVysílání"
    static let fallbackMessage = "Otevřete aplikaci iVysílání od České televize (najdete ji v App Store)."

    private static let appStorePages = [
        "com.apple.TVAppStore://itunes.apple.com/app/id436780264",
        "https://apps.apple.com/cz/app/id436780264",
    ].compactMap(URL.init(string:))

    /// Tries each App Store URL in turn; calls `onFailure` if none opens.
    static func open(onFailure: @escaping @MainActor () -> Void) {
        Task {
            for url in appStorePages {
                let opened = await UIApplication.shared.open(url)
                log.info("open \(url.absoluteString, privacy: .public) -> \(opened)")
                if opened { return }
            }
            onFailure()
        }
    }
}
