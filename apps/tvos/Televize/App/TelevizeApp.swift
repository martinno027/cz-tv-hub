import CTCore
import Foundation
import SwiftUI

@main
struct TelevizeApp: App {
    @State private var model = AppModel(client: Self.client())

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .preferredColorScheme(.dark)
        }
    }

    private static func client() -> CTClient {
        #if DEBUG
        // `-failSchedule YES` makes every ČT schedule request fail (no request is sent), for simulator checks of
        // "Program se nepodařilo načíst" in the guide and on Živě (spec/apps.md, T36).
        if UserDefaults.standard.bool(forKey: "failSchedule") {
            return CTClient(transport: FailingScheduleTransport())
        }
        // `-failScheduleSeconds 70` fails them only for that long after start, then answers them with a made-up
        // schedule (no request to ČT): an outage that ends while the app is open, for simulator checks of the retry
        // (spec/apps.md, T42) that do not depend on ČT answering.
        let seconds = UserDefaults.standard.integer(forKey: "failScheduleSeconds")
        if seconds > 0 {
            return CTClient(transport: FailingScheduleTransport(until: Date().addingTimeInterval(TimeInterval(seconds))))
        }
        #endif
        return CTClient()
    }
}

#if DEBUG
/// Fails ČT's schedule (GraphQL) requests without sending them (until `until`, nil = always); everything else goes
/// out as usual.
private struct FailingScheduleTransport: HTTPTransport {
    var until: Date?
    let base = URLSessionTransport()

    func send(_ request: URLRequest) async throws -> HTTPResponse {
        if request.url?.path.contains("graphql") == true {
            let fail = until.map { Date() < $0 } ?? true
            NSLog("failSchedule: ČT schedule request %@", fail ? "failed" : "answered with a made-up schedule")
            if fail { throw URLError(.notConnectedToInternet) }
            return Self.madeUpSchedule(now: Date())
        }
        return try await base.send(request)
    }

    /// Every ČT channel: "Ukázkový pořad" from an hour ago to an hour from now.
    private static func madeUpSchedule(now: Date) -> HTTPResponse {
        let format = ISO8601DateFormatter()
        let plans = Channels.all.map { channel in
            ["channel": channel.slug,
             "program": [["idec": "debug-\(channel.slug)", "title": "Ukázkový pořad",
                          "start": format.string(from: now.addingTimeInterval(-3600)),
                          "end": format.string(from: now.addingTimeInterval(3600))]]] as [String: Any]
        }
        let body = (try? JSONSerialization.data(withJSONObject: ["data": ["TVProgramDailyChannelsPlanV2": plans]])) ?? Data()
        return HTTPResponse(status: 200, body: body)
    }
}
#endif
