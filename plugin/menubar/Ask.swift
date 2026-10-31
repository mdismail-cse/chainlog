// The Ask tab: chat with Claude or Codex about your sessions. The app only draws and runs this; the thinking is done by
// `chainlog _ask` (see plugin/src/chainlog/ask.py), so the terminal's `chainlog ask` and this tab give the same answers.
import AppKit
import Foundation

enum AskEngine: String, CaseIterable, Identifiable {
    case claude, codex
    var id: String { rawValue }
    var label: String { self == .claude ? "Claude" : "Codex" }
    var other: AskEngine { self == .claude ? .codex : .claude }
}

/// What `chainlog ask --status --json` says about one tool.
struct AskEngineStatus: Decodable {
    let installed: Bool
    let signed_in: Bool?
    let detail: String
    /// Installed, and not known to be signed out (an older tool that cannot say is given the benefit of the doubt).
    var usable: Bool { installed && signed_in != false }
}

struct AskStatus: Decodable {
    let engine: String?
    let consented: [String]
    let model: String?
    let ready: Bool
    let engines: [String: AskEngineStatus]
    let disclosure: [String: String]
}

/// One line of what `chainlog _ask` prints: `status` lines while it works, then one `answer` or `error`.
struct AskEvent: Decodable {
    let type: String
    let text: String?
    let answer: String?
    let sessions: [Row]?
    let engine: String?
    let model: String?
    let calls: Int?
    let code: String?
    let message: String?
}

struct ChatMessage: Identifiable {
    enum Role { case user, assistant, error }
    let id = UUID()
    let role: Role
    var text: String
    var sessions: [Row] = []   // the sessions an answer mentions (the same rows the other tabs show)
    var meta: String? = nil    // "Claude · sonnet · 2 requests"
    var code: String? = nil    // an error's code, e.g. not_signed_in
}

/// A conversation in a file (`--chat-file`): [{"role": "user"|"assistant"|"error", "text": …, "sessions": [rows], "meta": …, "code": …}].
struct ChatMessageFile: Decodable {
    let role: String
    let text: String
    let sessions: [Row]?
    let meta: String?
    let code: String?
    var message: ChatMessage {
        ChatMessage(role: role == "user" ? .user : (role == "error" ? .error : .assistant), text: text, sessions: sessions ?? [], meta: meta, code: code)
    }
}

struct AskRequest {
    let question: String
    let history: [[String: String]]  // earlier messages: [{"role": "user"|"assistant", "text": …}]
    let engine: String?

    var json: Data {
        var o: [String: Any] = ["question": question, "history": history]
        if let e = engine { o["engine"] = e }
        return (try? JSONSerialization.data(withJSONObject: o)) ?? Data("{}".utf8)
    }
}

/// Stops a running request. Calling it twice is fine.
typealias AskCancel = () -> Void

// MARK: - running `chainlog _ask`

/// Splits what `chainlog _ask` prints into events (a line may arrive in pieces).
typealias AskLines = JSONLines<AskEvent>

enum AskRunner {
    /// Start `chainlog _ask`. `onEvent` gets each line it prints and `onDone` its exit status, both on the main queue and in
    /// order. nil when chainlog cannot be found. The returned closure stops it (SIGTERM: chainlog ends the AI process too).
    static func start(_ request: AskRequest, onEvent: @escaping @Sendable (AskEvent) -> Void,
                      onDone: @escaping @Sendable (Int32) -> Void) -> AskCancel? {
        StreamRunner.start(["_ask"], input: request.json, onEvent: onEvent, onDone: onDone)
    }

    /// `chainlog ask --status --json`: what is installed, signed in and connected. nil when it cannot be read.
    static func status() -> AskStatus? {
        guard let exe = CLI.locate() else { return nil }
        let r = CLI.run(exe, ["ask", "--status", "--json"], timeout: 45)
        guard r.status == 0, let out = r.out else { return nil }
        return try? JSONDecoder().decode(AskStatus.self, from: out)
    }
}

// MARK: - the text of an answer

enum AskText {
    static let scheme = "chainlog-session"
    private static let token = try! NSRegularExpression(pattern: "\\[\\[([0-9a-fA-F][0-9a-fA-F-]{1,39})\\]\\]")

    /// `[[id]]` replaced by what `name` says for that session; an id that is not one of `sessions` is dropped.
    private static func replacing(_ text: String, sessions: [Row], with name: (Row) -> String) -> String {
        let ns = text as NSString
        var out = ""
        var last = 0
        for m in token.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            let id = ns.substring(with: m.range(at: 1)).lowercased()
            if let row = sessions.first(where: { $0.short.lowercased() == id }) { out += name(row) }
            last = m.range.location + m.range.length
        }
        return out + ns.substring(from: last)
    }

    /// Characters that would turn a title into markdown.
    static func escape(_ s: String) -> String {
        var out = ""
        for ch in s {
            if "\\`*_[]()<>#!~|".contains(ch) { out.append("\\") }
            out.append(ch)
        }
        return out
    }

    /// The answer as text to read: **bold** and `code` work, and each mentioned session is a link that opens it. No other link
    /// ever works, whatever the AI wrote: a link in an answer could be a phishing one.
    static func attributed(_ text: String, sessions: [Row]) -> AttributedString {
        let md = replacing(text, sessions: sessions) { "[\(escape(clip($0.title, 30)))](\(scheme)://s/\($0.short))" }
        var a = (try? AttributedString(markdown: md, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(plain(text, sessions: sessions))
        let foreign = a.runs.filter { $0.link != nil && $0.link?.scheme != scheme }.map { $0.range }
        for r in foreign { a[r].link = nil }
        return a
    }

    /// The answer for the clipboard: each session named as `Title (id)`.
    static func plain(_ text: String, sessions: [Row]) -> String {
        replacing(text, sessions: sessions) { "\(clip($0.title, 40)) (\($0.short))" }
    }

    /// The short id a link made by `attributed` points at, or nil for any other URL.
    static func session(in url: URL) -> String? {
        url.scheme == scheme && url.host == "s" && url.pathComponents.count == 2 ? url.pathComponents[1] : nil
    }
}

// MARK: - the chat

@MainActor
final class ChatModel: ObservableObject {
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var thinking = false
    @Published private(set) var progress = ""            // "Asking Claude…"
    @Published var draft = ""
    @Published private(set) var status: AskStatus?        // what the connect screen shows
    @Published private(set) var checking = false
    @Published private(set) var connecting: AskEngine?
    weak var app: AppModel?

    /// How a question is run. Tests replace it.
    var starter: (AskRequest, @escaping @Sendable (AskEvent) -> Void, @escaping @Sendable (Int32) -> Void) -> AskCancel? = { AskRunner.start($0, onEvent: $1, onDone: $2) }
    /// How the status is read. Tests replace it.
    var statusReader: @Sendable () -> AskStatus? = { AskRunner.status() }
    /// Asked before you agree to send session text to a tool (title, what is sent). Tests replace it.
    var connectConfirmer: (String, String) -> Bool = { Alerts.confirm($0, $1, ok: "Connect") }
    /// Asked before the agreement is taken back. Tests replace it.
    var disconnectConfirmer: (String, String) -> Bool = { Alerts.confirm($0, $1, ok: "Disconnect") }
    /// Tests set this so a status check or a connect has finished when it returns.
    var inline = false

    private var cancel: AskCancel?
    private var generation = 0       // a request that was stopped must not write into the chat later
    private var gotFinal = false
    private var limitTask: Task<Void, Never>?
    static let giveUpAfter: UInt64 = 600_000_000_000

    static let suggestions = [
        "What did I work on this week?",
        "Which sessions failed or are still in progress?",
        "How did my newest PR chain end?",
        "Which sessions have no note yet?",
    ]

    // MARK: what is shown

    /// Chainlog sends `ask` only from the version that has the Ask tab.
    var supported: Bool { app?.data?.ask != nil }
    var connected: Bool { app?.data?.ask?.connected == true }
    var engine: AskEngine? { app?.data?.ask?.engine.flatMap(AskEngine.init(rawValue:)) }
    var hasConversation: Bool { !messages.isEmpty || thinking }

    /// The earlier messages, as chainlog wants them: only what was said (not errors), the last few.
    func historyPayload() -> [[String: String]] {
        messages.filter { $0.role != .error }.suffix(8).map { ["role": $0.role == .user ? "user" : "assistant", "text": $0.text] }
    }

    // MARK: asking

    func send(_ text: String? = nil) {
        let question = (text ?? draft).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, !thinking else { return }
        guard connected else { app?.say("Connect Claude or Codex first"); return }
        if text == nil { draft = "" }
        let history = historyPayload()
        messages.append(ChatMessage(role: .user, text: question))
        thinking = true
        gotFinal = false
        progress = "Starting…"
        generation += 1
        let gen = generation
        let request = AskRequest(question: question, history: history, engine: nil)
        cancel = starter(request, { event in
            MainActor.assumeIsolated { self.handle(event, gen) }  // called on the main queue
        }, { code in
            MainActor.assumeIsolated { self.finished(code, gen) }
        })
        if cancel == nil {
            thinking = false
            messages.append(ChatMessage(role: .error, text: "chainlog was not found, so there is nothing to ask.", code: "not_found"))
            return
        }
        limitTask?.cancel()
        limitTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: ChatModel.giveUpAfter)
            if Task.isCancelled { return }
            if let s = self, s.generation == gen { s.timedOut() }
        }
    }

    /// The request has run far longer than any answer should take: stop it and say so.
    func timedOut() {
        guard thinking else { return }
        cancel?()
        cancel = nil
        generation += 1
        thinking = false
        messages.append(ChatMessage(role: .error, text: "That took too long, so it was stopped. Try a narrower question.", code: "timeout"))
    }

    private func handle(_ e: AskEvent, _ gen: Int) {
        guard gen == generation else { return }
        switch e.type {
        case "status":
            if let t = e.text { progress = t }
        case "answer":
            gotFinal = true
            let who = AskEngine(rawValue: e.engine ?? "")?.label ?? "AI"
            let calls = e.calls ?? 0
            let meta = [who, e.model, calls > 0 ? "\(calls) request\(calls == 1 ? "" : "s")" : nil].compactMap { $0 }.joined(separator: " · ")
            messages.append(ChatMessage(role: .assistant, text: e.answer ?? "", sessions: e.sessions ?? [], meta: meta))
        case "error":
            gotFinal = true
            messages.append(ChatMessage(role: .error, text: e.message ?? "Something went wrong.", code: e.code))
            if ["not_connected", "no_consent", "not_installed", "not_signed_in"].contains(e.code ?? "") {
                app?.refresh()  // the Ask tab goes back to the connect screen, which says what is missing
                loadStatus()
            }
        default:
            break
        }
    }

    private func finished(_ code: Int32, _ gen: Int) {
        guard gen == generation, thinking else { return }
        thinking = false
        cancel = nil
        limitTask?.cancel()
        if !gotFinal {  // chainlog ended without an answer or an error: do not leave the chat waiting
            messages.append(ChatMessage(role: .error, text: "chainlog stopped before it could answer (exit \(code)). Run `chainlog doctor` in a terminal.", code: "failed"))
        }
    }

    func stop() {
        guard thinking else { return }
        cancel?()
        cancel = nil
        generation += 1  // whatever the stopped request still prints is ignored
        thinking = false
        limitTask?.cancel()
        messages.append(ChatMessage(role: .error, text: "Stopped.", code: "stopped"))
    }

    func clear() {
        stop()
        messages = []
    }

    // MARK: connecting

    func loadStatus() {
        guard !checking else { return }
        checking = true
        let read = statusReader
        if inline { status = read(); checking = false; return }
        DispatchQueue.global(qos: .userInitiated).async {
            let s = read()
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.status = s; self.checking = false }
            }
        }
    }

    /// What connecting means, from chainlog (one text for the terminal and the app).
    func disclosure(_ e: AskEngine) -> String { status?.disclosure[e.rawValue] ?? "Your questions and short excerpts of your sessions are sent to \(e.label). Secrets are redacted first." }

    /// Agree to send session text to `e` and use it. Asks first unless you already agreed to that tool.
    func connect(_ e: AskEngine) {
        guard connecting == nil else { return }
        let agreed = status?.consented.contains(e.rawValue) == true
        if !agreed && !connectConfirmer("Connect \(e.label)?", disclosure(e)) { return }
        connecting = e
        run(["ask", "--connect", e.rawValue, "--yes"], done: "Connected to \(e.label)", failed: "Could not connect")
    }

    func disconnect() {
        guard connected else { return }
        guard disconnectConfirmer("Disconnect?", "chainlog stops sending session text to Claude and Codex until you connect again. Your chat here is cleared.") else { return }
        clear()
        run(["ask", "--disconnect"], done: "Disconnected", failed: "Could not disconnect")
    }

    private func run(_ args: [String], done: String, failed: String) {
        guard let app = app else { return }
        let runner = app.runner
        let finish: @Sendable ((ok: Bool, message: String)) -> Void = { r in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    self.connecting = nil
                    app.say(r.ok ? done : failed + ": " + clip(r.message.components(separatedBy: "\n").last ?? "", 90))
                    app.refresh()
                    self.status = nil
                    self.loadStatus()
                }
            }
        }
        if inline { let r = runner(args); connecting = nil; app.say(r.ok ? done : failed + ": " + clip(r.message.components(separatedBy: "\n").last ?? "", 90)); app.refresh(); status = nil; loadStatus(); return }
        DispatchQueue.global(qos: .userInitiated).async { finish(runner(args)) }
    }

    /// A link in an answer was clicked: open that session, if it is one the answer came with. True when it was ours.
    func open(_ url: URL) -> Bool {
        guard let short = AskText.session(in: url) else { return false }
        for m in messages.reversed() {
            if let row = m.sessions.first(where: { $0.short == short }) { app?.open(row); return true }
        }
        return true  // ours, but not a session this chat knows: nothing happens
    }

    /// Debugging / screenshots / tests: show these messages, and this status, without asking anything.
    func preload(messages: [ChatMessage], status: AskStatus?) {
        self.messages = messages
        if let s = status { self.status = s }
    }

    func copy(_ m: ChatMessage) {
        app?.copier(AskText.plain(m.text, sessions: m.sessions))
        app?.say("Answer copied")
    }
}
