// The JSON `chainlog _menu` prints (schema chainlog.menu/1; see SCHEMA.md). Every field here that is not
// optional must always be present: tests/test_menubar.py reads these structs and checks the real output.
import Foundation

struct ResumeInfo: Decodable {
    let cwd: String
    let command: String
    let blockers: [String]
    let notes: [String]
}

struct Row: Decodable {
    let ref: String
    let short: String
    let harness: String
    let title: String
    let saved: String          // user | auto | none
    let status: String?
    let live: Bool
    var starred: Bool
    let chain: String?
    let round: Int?
    let ago: String
    let last_work: String?
    let resume: ResumeInfo
}

struct ChainInfo: Decodable {
    let id: String
    let rounds: Int
    let status: String?
    let title: String
    let ago: String
    var members: [Row]
}

struct Counts: Decodable {
    var unsaved: Int
    var starred: Int
    var chains: Int?    // all of them; `chains` lists the newest 30 (absent from an older chainlog)
}

struct MenuData: Decodable {
    let schema: String
    let chainlog: String
    var counts: Counts
    var unsaved: [Row]
    var recent: [Row]
    var starred: [Row]
    var chains: [ChainInfo]
}

/// The answer to `chainlog _menu --find TEXT`.
struct FindData: Decodable {
    let schema: String
    let query: String
    let results: [Row]
}

extension Row: Identifiable {
    var id: String { ref }

    /// The session's folder, with the home folder shortened to ~.
    var folder: String {
        let home = homeDirectory()
        return resume.cwd.hasPrefix(home) ? "~" + resume.cwd.dropFirst(home.count) : resume.cwd
    }

    /// The result as shown in the pill (free text from `chainlog status`, so long ones are shortened).
    var statusLabel: String? { status.map { clip($0, 12) } }

    /// Can this session be reopened? (Claude deletes old transcripts.)
    var canResume: Bool { resume.blockers.isEmpty }

    /// What chainlog itself warns about before reopening (a running session would get a second copy, an archived
    /// Codex session must be unarchived, the folder may be wrong). `chainlog resume` asks first; so does the app.
    var warning: String? { resume.notes.isEmpty ? nil : resume.notes.joined(separator: "\n") }

    /// "xcloud#8707 r2 · codex · 1h": the second line of a row (the status is shown as a pill next to the title).
    var detail: String {
        var parts: [String] = []
        if let c = chain { parts.append(clip(c, 30) + (round.map { " r\($0)" } ?? "")) }
        if harness != "claude" { parts.append(harness) }
        parts.append(ago)
        return parts.joined(separator: " · ")
    }

    /// Every word of `query` appears somewhere in the row (used while a real search is still loading).
    func matches(_ query: String) -> Bool {
        let hay = [title, chain ?? "", status ?? "", harness, last_work ?? ""].joined(separator: " ").lowercased()
        return query.lowercased().split(whereSeparator: { $0.isWhitespace }).allSatisfy { hay.contains($0) }
    }
}

extension ChainInfo: Identifiable {
    /// The name shown for a chain (chain names are free text, so a very long one is shortened).
    var label: String { clip(id, 34) }
}

/// The user's home folder. $HOME first (a GUI app gets it too), so a test or a different account can point it elsewhere.
func homeDirectory() -> String {
    ProcessInfo.processInfo.environment["HOME"].flatMap { $0.isEmpty ? nil : $0 } ?? NSHomeDirectory()
}

func clip(_ s: String, _ n: Int) -> String { s.count <= n ? s : String(s.prefix(n - 1)) + "…" }
