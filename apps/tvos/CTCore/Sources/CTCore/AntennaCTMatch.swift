import Foundation

/// spec/apps.md "ČT images for Anténa ČT channels": which CT programme an Anténa event on a ČT channel is,
/// so the guide can show the official ČT image without a recording. Commercial channels never match.
public enum AntennaCTMatch {
    static let droppedTokens: Set<String> = ["hd", "sd", "uhd", "4k", "t2", "hevc",
                                             "pha", "praha", "jm", "sm", "brno", "ostrava"]
    static let slugsByName: [String: [String]] = [
        "ct1": ["ct1"], "ct2": ["ct2"], "ct24": ["ct24"], "ctsport": ["sport"],
        "ct:d": ["decko"], "ctd": ["decko"], "ctart": ["art"],
        "ct:d/art": ["decko", "art"], "ctd/art": ["decko", "art"],
    ]

    static func fold(_ text: String) -> String {
        text.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "cs_CZ")).lowercased()
    }

    /// CT GraphQL slugs an Anténa channel name stands for ("CT 1 PHA HD T2" → ["ct1"]); [] for others.
    public static func slugs(channelName: String) -> [String] {
        let key = fold(channelName).split(whereSeparator: \.isWhitespace)
            .filter { !droppedTokens.contains(String($0)) }
            .joined()
        return slugsByName[key] ?? []
    }

    static func normalizedTitle(_ title: String) -> String {
        String(fold(title).unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) && $0.isASCII }
            .map(Character.init))
    }

    /// The CT programme for an Anténa event: same normalised title among the overlapping ones, else the one
    /// with the largest overlap if it covers at least half of the event; nil otherwise.
    public static func programme(start: Date, end: Date, title: String, channelName: String,
                                 schedule: [String: [Programme]]) -> Programme? {
        let candidates = slugs(channelName: channelName).flatMap { schedule[$0] ?? [] }
            .filter { $0.end > start && $0.start < end }
        guard !candidates.isEmpty else { return nil }
        let wanted = normalizedTitle(title)
        if let same = candidates.first(where: { normalizedTitle($0.title) == wanted }) { return same }
        func overlap(_ p: Programme) -> TimeInterval { min(end, p.end).timeIntervalSince(max(start, p.start)) }
        guard let best = candidates.max(by: { overlap($0) < overlap($1) }),
              overlap(best) >= end.timeIntervalSince(start) / 2 else { return nil }
        return best
    }

    public static func programme(for event: DVBEvent, channel: DVBChannel, schedule: [String: [Programme]]) -> Programme? {
        programme(start: event.start, end: event.end, title: event.title, channelName: channel.name, schedule: schedule)
    }
}
