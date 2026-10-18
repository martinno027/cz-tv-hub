import Foundation
@testable import CTCore

/// Loads the shared fixtures in `spec/fixtures` (`{"status": …, "body": …}` wrappers).
enum Fixture {
    /// Found by walking up from this file, so the app directory can move within the repo.
    static let directory: URL = {
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        while dir.path != "/" {
            let candidate = dir.appendingPathComponent("spec/fixtures")
            var isDirectory: ObjCBool = false
            if FileManager.default.fileExists(atPath: candidate.path, isDirectory: &isDirectory), isDirectory.boolValue {
                return candidate
            }
            dir = dir.deletingLastPathComponent()
        }
        fatalError("spec/fixtures not found above \(#filePath)")
    }()

    static func json(_ name: String) throws -> [String: Any] {
        let data = try Data(contentsOf: directory.appendingPathComponent("\(name).json"))
        return try JSONSerialization.jsonObject(with: data) as! [String: Any]
    }

    static func response(_ name: String) throws -> HTTPResponse {
        let wrapper = try json(name)
        let body = try JSONSerialization.data(withJSONObject: wrapper["body"]!, options: [.fragmentsAllowed])
        return HTTPResponse(status: wrapper["status"] as! Int, body: body)
    }
}

func utc(_ text: String) -> Date {
    try! Date.ISO8601FormatStyle().parse(text)
}

/// Serves canned responses by URL substring and records every request.
final class FakeTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var routes: [(match: String, result: Result<HTTPResponse, Error>)] = []
    private var _requests: [URLRequest] = []

    var requests: [URLRequest] { lock.withLock { _requests } }

    func on(_ match: String, _ response: HTTPResponse) {
        lock.withLock { routes.append((match, .success(response))) }
    }

    func on(_ match: String, fixture: String) throws {
        on(match, try Fixture.response(fixture))
    }

    func fail(_ match: String, _ error: Error) {
        lock.withLock { routes.append((match, .failure(error))) }
    }

    func send(_ request: URLRequest) async throws -> HTTPResponse {
        let route: Result<HTTPResponse, Error>? = lock.withLock {
            _requests.append(request)
            let key = request.url!.absoluteString + (request.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? "")
            // Last registered route wins, so tests can override.
            return routes.last { key.contains($0.match) }?.result
        }
        guard let route else { throw URLError(.badServerResponse) }
        return try route.get()
    }
}

final class MutableClock: @unchecked Sendable {
    private let lock = NSLock()
    private var _now: Date

    init(_ now: Date) { _now = now }

    var now: Date {
        get { lock.withLock { _now } }
        set { lock.withLock { _now = newValue } }
    }
}
