// The JSON `chainlog _menu` prints (schema chainlog.menu/1; see SCHEMA.md). Every field here that is not
// optional must always be present: tests/test_menubar.py reads these structs and checks the real output.
import Foundation

struct ResumeInfo: Decodable {
    let cwd: String
    let command: String
    let vscode: String?  // shell command that reopens it in the VS Code extension (only for sessions started there)
    let session_id: String?  // the tool's own id for the session (absent from an older chainlog)
    let link: String?        // the extension's vscode:// link, to paste somewhere else (only for sessions started in VS Code)
    let restores: Bool?  // Claude deleted the original; opening puts chainlog's kept copy back first
    let blockers: [String]
    let notes: [String]
}

struct Row: Decodable {
    let ref: String
    let short: String
    let harness: String
    let via: String?           // "VS Code" | "Desktop" | nil (a plain terminal session): where it was started
    let title: String
    let saved: String          // user | auto | none
    let status: String?
    let live: Bool
    var starred: Bool
    let kept: Bool?            // chainlog keeps a copy that Claude's cleanup cannot delete
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
    var all: Int?       // every session chainlog lists (absent from an older chainlog)
}

/// How long Claude Code keeps transcripts and what is about to go (the ⚙ menu). Absent from an older chainlog.
struct RetentionInfo: Decodable {
    let days: Int
    let is_default: Bool
    let expiring: Int
    let expiring_unkept: Int
    let expiring_within: Int
    var title: String { "Claude keeps sessions for \(days) days\(is_default ? " (default)" : "")…" }
    /// Only when something that expires soon has no kept copy: a session that is kept is not at risk, so it is no alarm.
    var warning: String? {
        expiring_unkept > 0 ? "\(expiring) \(expiring == 1 ? "expires" : "expire") within \(expiring_within) days (\(expiring_unkept) without a kept copy)" : nil
    }
}

/// What `chainlog retention DAYS --json` says a change would do (it writes nothing).
struct RetentionPlan: Decodable {
    let current: Int
    let to: Int
    let changed: Bool
    let would_delete: Int
    let would_delete_unkept: Int
}

/// Whether the Ask tab can chat: the tool you chose, and that you agreed to send it session text. Absent from an older chainlog.
struct AskConfig: Decodable {
    let engine: String?
    let connected: Bool
}

/// One context (`chainlog context`): what many sessions taught you, in one file a new session reads when it needs it.
struct ContextInfo: Decodable {
    let name: String
    let slug: String
    let sessions: Int
    let built: Bool
    let ago: String            // since it was built
    let period: String         // "2026-08-03 to 2026-10-28"
    let gist: String           // one sentence: what the work is and where it stands
    let chain: String?         // the chain it follows (its new rounds join it)
    let questions: [String]
    let engine: String?        // what built it
    let pending: ContextPending
    let needs_update: Bool     // built, and something new or changed is waiting
    let file: String           // CONTEXT.md
    let dir: String
    let reference: String      // the lines that attach it to a session (empty until it is built)
    let start: ContextStart    // where a new session starts by default
    let members: [String]      // session refs
}

struct ContextPending: Decodable {
    let new: Int
    let changed: Int
    let questions: Bool
    let text: String           // "3 new sessions, 1 changed"
}

struct ContextStart: Decodable {
    let tool: String
    let folder: String?
}

extension ContextInfo: Identifiable {
    var id: String { slug }
    var tool: AskEngine { AskEngine(rawValue: start.tool) ?? .claude }
    /// What waits for Update, short enough for a pill: "3 new", "1 changed", "new questions".
    var pendingShort: String {
        if pending.new > 0 { return "\(pending.new) new" }
        if pending.changed > 0 { return "\(pending.changed) changed" }
        return pending.questions ? "new questions" : "update"
    }
}

struct MenuData: Decodable {
    let schema: String
    let chainlog: String
    var counts: Counts
    var unsaved_window_hours: Int?  // "unsaved" means: ended within this many hours, after chainlog was installed
    var retention: RetentionInfo?
    var keep_auto: Bool?            // saving or starring a session keeps a copy of it
    var ask: AskConfig?
    var contexts: [ContextInfo]?        // absent from an older chainlog
    var context_questions: [String]?    // what a new context asks each session (yours, or chainlog's ten)
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
    var isKept: Bool { kept ?? false }
    var restores: Bool { resume.restores ?? false }

    /// What chainlog itself warns about before reopening (a running session would get a second copy, an archived
    /// Codex session must be unarchived, the folder may be wrong). `chainlog resume` asks first; so does the app.
    var warning: String? { resume.notes.isEmpty ? nil : resume.notes.joined(separator: "\n") }

    /// "xcloud#8707 r2 · codex · 1h": the second line of a row (the status is shown as a pill next to the title).
    var detail: String {
        var parts: [String] = []
        if let c = chain { parts.append(clip(c, 30) + (round.map { " r\($0)" } ?? "")) }
        if harness != "claude" { parts.append(harness) }
        if let v = via { parts.append(v) }
        if isKept { parts.append("kept") }
        parts.append(ago)
        return parts.joined(separator: " · ")
    }

    /// Everything a search looks at in this row, lower-cased.
    var haystack: String {
        [title, chain ?? "", status ?? "", harness, via ?? "", via == "VS Code" ? "vscode" : "", last_work ?? ""].joined(separator: " ").lowercased()
    }

    /// Every word of `query` appears somewhere in the row (used while a real search is still loading).
    func matches(_ query: String) -> Bool {
        let hay = haystack
        return query.lowercased().split(whereSeparator: { $0.isWhitespace }).allSatisfy { hay.contains($0) }
    }
}

extension ChainInfo: Identifiable {
    /// The name shown for a chain (chain names are free text, so a very long one is shortened).
    var label: String { clip(id, 34) }
    /// The round "Continue the latest round" opens: the newest one, and only when it can be opened (an older round
    /// is not offered in its place: that would continue the wrong work).
    var continuable: Row? { members.last.flatMap { $0.canResume ? $0 : nil } }
}

/// The user's home folder. $HOME first (a GUI app gets it too), so a test or a different account can point it elsewhere.
func homeDirectory() -> String {
    ProcessInfo.processInfo.environment["HOME"].flatMap { $0.isEmpty ? nil : $0 } ?? NSHomeDirectory()
}

func clip(_ s: String, _ n: Int) -> String { s.count <= n ? s : String(s.prefix(n - 1)) + "…" }
