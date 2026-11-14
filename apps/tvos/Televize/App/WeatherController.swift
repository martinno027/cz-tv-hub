import CoreLocation
import CTCore
import Foundation
import Observation

/// Domů's weather (spec/apps.md "Domů", T59): `/dvb/weather` from the household gateway, with this TV's location
/// rounded to 0.1° when the household allowed it once ("Počasí podle polohy této televize"). Asked again only when
/// Domů is shown and the last answer is older than 30 minutes; nothing in the background. The location and the
/// answer are kept in memory only; the one thing stored is the switch.
@MainActor
@Observable
final class WeatherController {
    /// Nil = nothing to show (no gateway, weather off on the gateway, an error): the header shows the date only.
    private(set) var weather: Weather?
    /// The question is on screen on Domů.
    var showsConsent = false
    /// "Povolit" / "Nepoužívat" stored, Back for this run only; the switch in Nastavení → Server is `usesLocation`.
    private(set) var consent: WeatherConsent

    @ObservationIgnored private var client: DVBClient?
    @ObservationIgnored private var fetchedAt: Date?
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored private let locator = DeviceLocator()
    private static let key = "weatherUseLocation"

    init() {
        #if DEBUG
        // `-weatherConsentReset YES`: never answered, for the UI test of the question
        if UserDefaults.standard.bool(forKey: "weatherConsentReset") { UserDefaults.standard.removeObject(forKey: Self.key) }
        #endif
        consent = WeatherConsent(stored: UserDefaults.standard.object(forKey: Self.key) as? Bool)
    }

    /// The gateway changed (or went away): what was shown belongs to the old one.
    func setClient(_ client: DVBClient?) {
        self.client = client
        loading?.cancel()
        loading = nil
        weather = nil
        fetchedAt = nil
    }

    /// Domů is on screen: the question until it is answered or put away in this run (with a gateway, where weather can
    /// come from), else the weather when the last answer is older than `WeatherRules.maxAge`.
    func domuShown() async {
        guard client != nil else { return }
        guard !consent.asksOnDomu else {
            showsConsent = true
            return
        }
        guard loading == nil, WeatherRules.needsRefresh(fetchedAt: fetchedAt, now: Date()) else {
            await loading?.value
            return
        }
        let useLocation = consent.usesLocation  // the OS is asked only after "Povolit"
        let task = Task { await load(useLocation: useLocation) }
        loading = task
        await task.value
        if loading == task { loading = nil }
    }

    /// The question answered (Domů or Nastavení); Back stores nothing. A changed answer is used by the next Domů.
    func answer(_ answer: WeatherConsent.Answer) {
        showsConsent = false
        let before = consent.usesLocation
        consent.answer(answer)
        if let stored = consent.stored { UserDefaults.standard.set(stored, forKey: Self.key) }
        if consent.usesLocation != before { invalidate() }
    }

    /// The switch in Nastavení → Server turned off: at once, no question.
    func switchOff() {
        consent.switchOff()
        UserDefaults.standard.set(false, forKey: Self.key)
        invalidate()
    }

    private func invalidate() {
        loading?.cancel()
        loading = nil
        fetchedAt = nil
    }

    private func load(useLocation: Bool) async {
        guard let client else { return }
        let location = useLocation ? await locator.current().flatMap {
            WeatherRules.round(lat: $0.coordinate.latitude, lon: $0.coordinate.longitude)
        } : nil
        do {
            let weather = try await client.weather(location: location)
            guard !Task.isCancelled, client.config == self.client?.config else { return }
            self.weather = weather
            fetchedAt = Date()
        } catch {
            // no answer: nothing shown, asked again the next time Domů appears
            guard !Task.isCancelled, client.config == self.client?.config else { return }
            log.info("weather: \(String(describing: error), privacy: .public)")
            weather = nil
        }
    }
}

/// One location fix from CoreLocation, "when in use" with reduced accuracy; nil when refused, unavailable or slow.
@MainActor
private final class DeviceLocator: NSObject, CLLocationManagerDelegate {
    private static let timeout: Duration = .seconds(15)

    private let manager = CLLocationManager()
    private var authorizationWaiters: [CheckedContinuation<Void, Never>] = []
    private var locationWaiters: [CheckedContinuation<CLLocation?, Never>] = []

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyReduced
    }

    func current() async -> CLLocation? {
        if manager.authorizationStatus == .notDetermined {
            manager.requestWhenInUseAuthorization()
            await withCheckedContinuation { authorizationWaiters.append($0) }
        }
        guard [.authorizedWhenInUse, .authorizedAlways].contains(manager.authorizationStatus) else { return nil }
        let timeout = Task { [weak self] in
            try? await Task.sleep(for: Self.timeout)
            guard !Task.isCancelled else { return }
            self?.finish(nil)
        }
        defer { timeout.cancel() }
        return await withCheckedContinuation { waiter in
            locationWaiters.append(waiter)
            if locationWaiters.count == 1 { manager.requestLocation() }
        }
    }

    private func finish(_ location: CLLocation?) {
        let waiters = locationWaiters
        locationWaiters = []
        waiters.forEach { $0.resume(returning: location) }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        MainActor.assumeIsolated {
            guard status != .notDetermined else { return }
            let waiters = authorizationWaiters
            authorizationWaiters = []
            waiters.forEach { $0.resume() }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        MainActor.assumeIsolated { finish(locations.last) }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        MainActor.assumeIsolated { finish(nil) }
    }
}
