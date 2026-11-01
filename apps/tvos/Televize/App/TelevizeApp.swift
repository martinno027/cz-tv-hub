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
        #endif
        return CTClient()
    }
}

#if DEBUG
/// Fails ČT's schedule (GraphQL) requests without sending them; everything else goes out as usual.
private struct FailingScheduleTransport: HTTPTransport {
    let base = URLSessionTransport()

    func send(_ request: URLRequest) async throws -> HTTPResponse {
        if request.url?.path.contains("graphql") == true { throw URLError(.notConnectedToInternet) }
        return try await base.send(request)
    }
}
#endif
