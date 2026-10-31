// Contexts: what many sessions taught you, merged into one file that a new session reads only when it needs it. The app
// picks the sessions and the questions and shows the progress; `chainlog _context` does the reading and the merging
// (plugin/src/chainlog/context.py), so the terminal's `chainlog context` and the Chains tab always agree.
import AppKit
import Foundation

/// One line of what `chainlog _context` prints: `status` lines while it reads, then one `done` or `error`.
struct ContextEvent: Decodable {
    let type: String
    let text: String?
    let done: Int?
    let total: Int?
    let requests: Int?
    let up_to_date: Bool?
    let context: ContextInfo?
    let code: String?
    let message: String?
    let engine: String?
}

struct ContextRequest: Equatable {
    var name: String
    var isNew: Bool
    var refs: [String] = []
    var questions: [String]? = nil
    var chain: String? = nil
    var agree = false

    var json: Data {
        var o: [String: Any] = ["name": name, "new": isNew, "refs": refs, "agree": agree]
        if let q = questions { o["questions"] = q }
        if let c = chain { o["chain"] = c }
        return (try? JSONSerialization.data(withJSONObject: o)) ?? Data("{}".utf8)
    }
}

/// What `chainlog context use NAME --json` decided: the command that starts the session, and where.
struct ContextLaunch: Decodable {
    let command: String
    let tool: String
    let cwd: String
    let session_id: String?
}

// MARK: - running chainlog

/// Splits what a process prints into lines and reads each as `T`. A line may arrive in pieces.
final class JSONLines<T: Decodable>: @unchecked Sendable {
    private var buffer = Data()
    func feed(_ d: Data) -> [T] {
        buffer.append(d)
        var out: [T] = []
        while let nl = buffer.firstIndex(of: 10) {
            let line = buffer[buffer.startIndex..<nl]
            buffer = Data(buffer[buffer.index(after: nl)...])
            if let e = try? JSONDecoder().decode(T.self, from: line) { out.append(e) }
        }
        return out
    }
    /// What is left when the process has ended (a last line without its newline).
    func finish() -> [T] {
        defer { buffer = Data() }
        return (try? JSONDecoder().decode(T.self, from: buffer)).map { [$0] } ?? []
    }
}

enum StreamRunner {
    /// Start `chainlog ARGS` with `input` on stdin. `onEvent` gets each line it prints and `onDone` its exit status, both
    /// on the main queue and in order. nil when chainlog cannot be found. The returned closure stops it (SIGTERM).
    static func start<T: Decodable>(_ args: [String], input: Data, onEvent: @escaping @Sendable (T) -> Void,
                                    onDone: @escaping @Sendable (Int32) -> Void) -> AskCancel? {
        guard let exe = CLI.locate() else { return nil }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        p.environment = CLI.environment()
        let out = Pipe(), stdin = Pipe()
        p.standardOutput = out
        p.standardInput = stdin
        p.standardError = FileHandle.nullDevice
        let lines = JSONLines<T>()
        let finished = DispatchGroup()
        finished.enter()  // output closed
        finished.enter()  // process ended
        let deliver: @Sendable ([T]) -> Void = { events in
            guard !events.isEmpty else { return }
            DispatchQueue.main.async { events.forEach(onEvent) }
        }
        out.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if d.isEmpty {
                h.readabilityHandler = nil
                deliver(lines.finish())
                finished.leave()
                return
            }
            deliver(lines.feed(d))
        }
        p.terminationHandler = { _ in finished.leave() }
        do { try p.run() } catch {
            out.fileHandleForReading.readabilityHandler = nil
            return nil
        }
        stdin.fileHandleForWriting.write(input)
        try? stdin.fileHandleForWriting.close()
        finished.notify(queue: .main) { onDone(p.terminationStatus) }
        return {
            guard p.isRunning else { return }
            p.terminate()
            DispatchQueue.global().asyncAfter(deadline: .now() + 3) { if p.isRunning { kill(p.processIdentifier, SIGKILL) } }
        }
    }
}

enum ContextRunner {
    static func start(_ r: ContextRequest, onEvent: @escaping @Sendable (ContextEvent) -> Void,
                      onDone: @escaping @Sendable (Int32) -> Void) -> AskCancel? {
        StreamRunner.start(["_context"], input: r.json, onEvent: onEvent, onDone: onDone)
    }

    /// `chainlog context use SLUG --json --tool T [--folder DIR] [-- TASK]`: `--` keeps a task that starts with a dash a task.
    static func useArguments(slug: String, tool: AskEngine, folder: String?, task: String?) -> [String] {
        var a = ["context", "use", slug, "--json", "--tool", tool.rawValue]
        if let f = folder { a += ["--folder", f] }
        if let t = task?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { a += ["--", t] }
        return a
    }

    /// The launch chainlog planned, or what it said instead.
    static func launch(_ args: [String]) -> (ContextLaunch?, String) {
        guard let exe = CLI.locate() else { return (nil, "chainlog was not found") }
        let r = CLI.run(exe, args, timeout: 30, mergeStderr: true)
        let text = r.out.flatMap { String(data: $0, encoding: .utf8) } ?? ""
        if r.status == 0, let line = text.split(separator: "\n").last(where: { $0.hasPrefix("{") }),
           let l = try? JSONDecoder().decode(ContextLaunch.self, from: Data(line.utf8)) {
            return (l, "")
        }
        let said = text.split(separator: "\n").map { $0.replacingOccurrences(of: "chainlog: ", with: "") }.joined(separator: " ")
        return (nil, said.isEmpty ? "chainlog could not start it" : said)
    }

    @MainActor
    static func pickFolder() -> String? {
        NSApp.activate(ignoringOtherApps: true)
        let p = NSOpenPanel()
        p.canChooseDirectories = true
        p.canChooseFiles = false
        p.allowsMultipleSelection = false
        p.prompt = "Start here"
        p.message = "Where should the new session start?"
        return p.runModal() == .OK ? p.url?.path : nil
    }
}

// MARK: - the model

@MainActor
final class ContextModel: ObservableObject {
    enum Screen: Equatable { case list, builder, building, done, failed }

    @Published private(set) var screen: Screen = .list
    // the builder
    @Published var name = ""
    @Published private(set) var selected: [Row] = []
    @Published var pickQuery = "" { didSet { if pickQuery != oldValue { pickQueryChanged() } } }
    @Published private(set) var pickResults: [Row]?
    @Published private(set) var pickSearching = false
    @Published var questions: [String] = []
    @Published var showQuestions = false
    @Published var saveAsDefault = false
    @Published private(set) var target: ContextInfo?   // the context being added to; nil while making a new one
    @Published private(set) var chain: String?          // a new context that follows this chain
    // building
    @Published private(set) var progressText = ""
    @Published private(set) var progressDone = 0
    @Published private(set) var progressTotal = 0
    @Published private(set) var result: ContextInfo?
    @Published private(set) var resultRequests = 0
    @Published private(set) var resultUpToDate = false
    @Published private(set) var failure = ""
    @Published private(set) var building = ""           // the name of what is being built
    @Published var expanded: [String: Bool] = [:]       // which contexts are open in the list
    weak var app: AppModel?

    /// How a build is run. Tests replace it.
    var starter: (ContextRequest, @escaping @Sendable (ContextEvent) -> Void, @escaping @Sendable (Int32) -> Void) -> AskCancel? = { ContextRunner.start($0, onEvent: $1, onDone: $2) }
    /// Asked before whole sessions are first sent to a tool (title, what is sent). Tests replace it.
    var consentConfirmer: (String, String) -> Bool = { Alerts.confirm($0, $1, ok: "Agree and build") }
    /// How the session picker searches. Tests replace it.
    var finder: @Sendable (String) -> [Row]? = { CLI.find($0, everyWord: true) }  // "Pick all" must not take near misses
    /// Plans a launch (`chainlog context use … --json`). Tests replace it.
    var launchPlanner: @Sendable ([String]) -> (ContextLaunch?, String) = { ContextRunner.launch($0) }
    /// Asks where a session should start. Tests replace it.
    var folderPicker: () -> String? = { ContextRunner.pickFolder() }
    /// Tests set this so a launch or a search has finished when it returns.
    var inline = false

    private var cancel: AskCancel?
    private var generation = 0
    private var lastRequest: ContextRequest?
    private var pickTask: Task<Void, Never>?
    private var gotFinal = false

    // MARK: what is shown

    var supported: Bool { app?.data?.contexts != nil }
    var contexts: [ContextInfo] { app?.data?.contexts ?? [] }
    var defaultQuestions: [String] { app?.data?.context_questions ?? [] }
    func isOpen(_ c: ContextInfo) -> Bool { expanded[c.slug] ?? false }
    func toggleOpen(_ c: ContextInfo) { expanded[c.slug] = !isOpen(c) }

    /// Who builds it: the tool the Ask tab is connected to, else Claude.
    var engine: AskEngine { app?.data?.ask?.engine.flatMap(AskEngine.init(rawValue:)) ?? .claude }

    var cleanQuestions: [String] {
        var out: [String] = []
        for q in questions.map({ $0.trimmingCharacters(in: .whitespacesAndNewlines) }) where !q.isEmpty && !out.contains(q) { out.append(q) }
        return out
    }
    private var alreadyIn: Set<String> { Set(target?.members ?? []) }
    /// What a build would add: on a new context, everything picked; on an existing one, what is not in it yet.
    var adding: [Row] { selected.filter { !alreadyIn.contains($0.ref) } }
    var questionsChanged: Bool { target.map { cleanQuestions != $0.questions } ?? false }

    /// Why Build is not possible yet, or nil when it is.
    var blocker: String? {
        if cleanQuestions.isEmpty { return "Add at least one question" }
        if cleanQuestions.count > 15 { return "At most 15 questions" }
        if target == nil {
            if name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Give it a name" }
            if selected.isEmpty { return "Pick the sessions to put in it" }
            return nil
        }
        return adding.isEmpty && !questionsChanged ? "Pick sessions to add" : nil
    }

    /// "Reads 5 sessions with Claude: about 6 requests."
    var estimate: String {
        let n: Int
        if let t = target { n = questionsChanged ? t.sessions + adding.count : adding.count } else { n = selected.count }
        return "Reads \(n) session\(n == 1 ? "" : "s") with \(engine.label): about \(n + 1) request\(n == 0 ? "" : "s") on your plan."
    }

    /// The sessions the picker lists: a search's results, or (with no search) what you picked, then your newest sessions.
    var candidates: [Row] {
        if !pickQuery.trimmingCharacters(in: .whitespaces).isEmpty { return pickResults ?? [] }
        var rows = selected
        var seen = Set(rows.map(\.ref)).union(alreadyIn)
        for r in (app?.data?.unsaved ?? []) + (app?.data?.recent ?? []) where !seen.contains(r.ref) {
            rows.append(r)
            seen.insert(r.ref)
            if rows.count >= 60 { break }
        }
        return rows
    }

    func isSelected(_ r: Row) -> Bool { selected.contains { $0.ref == r.ref } }
    func isMember(_ r: Row) -> Bool { alreadyIn.contains(r.ref) }

    // MARK: the builder

    /// A new context from these sessions (and every round of `chain`, which it then follows).
    func beginNew(rows: [Row] = [], chain: ChainInfo? = nil) {
        target = nil
        self.chain = chain?.id
        name = chain?.label ?? ""
        var picked: [Row] = []
        for r in rows + (chain?.members ?? []) where !picked.contains(where: { $0.ref == r.ref }) { picked.append(r) }
        selected = picked
        questions = defaultQuestions
        resetBuilder()
        app?.tab = .chains
        screen = .builder
    }

    /// Add sessions to a context that exists (or change its questions).
    func beginAdd(to c: ContextInfo, rows: [Row] = []) {
        target = c
        chain = nil
        name = c.name
        selected = rows.filter { !c.members.contains($0.ref) }
        questions = c.questions
        resetBuilder()
        app?.tab = .chains
        screen = .builder
    }

    private func resetBuilder() {
        pickTask?.cancel()
        pickQuery = ""
        pickResults = nil
        pickSearching = false
        showQuestions = false
        saveAsDefault = false
        failure = ""
    }

    func closeBuilder() {
        pickTask?.cancel()
        screen = .list
    }

    func toggle(_ r: Row) {
        if isMember(r) { return }
        if let i = selected.firstIndex(where: { $0.ref == r.ref }) { selected.remove(at: i) } else { selected.append(r) }
    }

    /// Every row the search found (the quick way to take all 20 backup sessions).
    func selectAll(_ rows: [Row]) {
        for r in rows where !isSelected(r) && !isMember(r) { selected.append(r) }
    }

    func clearSelection() { selected = [] }

    func setQuestion(_ i: Int, _ text: String) { if questions.indices.contains(i) { questions[i] = text } }
    func removeQuestion(_ i: Int) { if questions.indices.contains(i) { questions.remove(at: i) } }
    func addQuestion() { questions.append("") }
    func resetQuestions() { questions = target?.questions ?? defaultQuestions }

    private func pickQueryChanged() {
        pickTask?.cancel()
        let q = pickQuery.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { pickResults = nil; pickSearching = false; return }
        pickSearching = true
        if inline { answerPick(q, finder(q)); return }
        let find = finder
        pickTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 250_000_000)
            if Task.isCancelled { return }
            let rows = await Task.detached { find(q) }.value
            if Task.isCancelled { return }
            self?.answerPick(q, rows)
        }
    }

    private func answerPick(_ q: String, _ rows: [Row]?) {
        guard q == pickQuery.trimmingCharacters(in: .whitespaces) else { return }
        pickResults = rows ?? []
        pickSearching = false
    }

    // MARK: building

    func build() {
        if let why = blocker { app?.say(why); return }
        let qs = cleanQuestions
        if saveAsDefault, let run = app?.runner {
            DispatchQueue.global().async { _ = run(["context", "questions", "--set"] + qs) }
        }
        let req: ContextRequest
        if let t = target {
            req = ContextRequest(name: t.slug, isNew: false, refs: adding.map(\.ref), questions: qs == t.questions ? nil : qs)
        } else {
            req = ContextRequest(name: name.trimmingCharacters(in: .whitespacesAndNewlines), isNew: true, refs: selected.map(\.ref),
                                 questions: qs, chain: chain)
        }
        building = target?.name ?? req.name
        run(req)
    }

    /// Read what is new or changed in a context, and merge again.
    func update(_ c: ContextInfo) {
        target = c
        building = c.name
        run(ContextRequest(name: c.slug, isNew: false))
    }

    private func run(_ req: ContextRequest) {
        lastRequest = req
        generation += 1
        let gen = generation
        gotFinal = false
        progressText = "Starting…"
        progressDone = 0
        progressTotal = 0
        failure = ""
        screen = .building
        cancel = starter(req, { e in
            MainActor.assumeIsolated { self.handle(e, gen) }  // called on the main queue
        }, { code in
            MainActor.assumeIsolated { self.finished(code, gen) }
        })
        if cancel == nil { fail("chainlog was not found, so nothing can be built.") }
    }

    private func handle(_ e: ContextEvent, _ gen: Int) {
        guard gen == generation else { return }
        switch e.type {
        case "status":
            if let t = e.text { progressText = t }
            progressDone = e.done ?? progressDone
            progressTotal = e.total ?? progressTotal
        case "done":
            gotFinal = true
            result = e.context
            resultRequests = e.requests ?? 0
            resultUpToDate = e.up_to_date ?? false
            if let c = e.context { expanded[c.slug] = true }
            screen = .done
            app?.refresh()
        case "error":
            gotFinal = true
            if e.code == "no_consent", var req = lastRequest, !req.agree {
                let who = AskEngine(rawValue: e.engine ?? "")?.label ?? "the AI"
                if consentConfirmer("Send these sessions to \(who)?", e.message ?? "") {
                    req.agree = true
                    req.isNew = false  // it was created before the agreement was asked for
                    run(req)
                    return
                }
                fail("Nothing was sent. The context is saved without its notes: build it whenever you agree.")
                app?.refresh()
                return
            }
            if let req = lastRequest, req.isNew, ["exists", "bad_request"].contains(e.code ?? "") {
                screen = .builder  // nothing was made: fix the name or the sessions and build again
                app?.say(clip(e.message ?? "Check the name", 90))
                return
            }
            fail(e.message ?? "Something went wrong.")
            app?.refresh()
        default:
            break
        }
    }

    private func finished(_ code: Int32, _ gen: Int) {
        guard gen == generation else { return }
        cancel = nil
        if !gotFinal { fail(code == 0 ? "chainlog stopped without an answer." : "chainlog stopped (exit \(code)).") }
    }

    private func fail(_ message: String) {
        failure = message
        screen = .failed
    }

    /// Stop a build. What was read so far is kept, so Update carries on from there.
    func stop() {
        guard screen == .building else { return }
        cancel?()
        cancel = nil
        generation += 1
        screen = .list
        app?.say("Stopped. What was read is kept: Update carries on")
        app?.refresh()
    }

    /// Try the last build again.
    func retry() {
        guard let req = lastRequest else { screen = .list; return }
        var r = req
        r.isNew = false  // a new context exists once its first build was started
        run(r)
    }

    func doneWithResult() {
        screen = .list
        result = nil
    }

    // MARK: using a context

    func start(_ c: ContextInfo, tool: AskEngine? = nil, folder: String? = nil, task: String? = nil) {
        guard c.built else { app?.say("Build it first"); return }
        let t = tool ?? c.tool
        let args = ContextRunner.useArguments(slug: c.slug, tool: t, folder: folder, task: task)
        let plan = launchPlanner
        let go: @Sendable ((ContextLaunch?, String)) -> Void = { r in
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.launched(c, t, r) }
            }
        }
        if inline { launched(c, t, plan(args)); return }
        app?.say("Starting \(t.label) with \(clip(c.name, 24))…")
        DispatchQueue.global(qos: .userInitiated).async { go(plan(args)) }
    }

    private func launched(_ c: ContextInfo, _ tool: AskEngine, _ r: (ContextLaunch?, String)) {
        guard let plan = r.0, let app = app else { app?.say("Could not start it: " + clip(r.1, 80)); return }
        if !app.launcher(plan.command, "context-" + c.slug, {
            MainActor.assumeIsolated { app.say("Could not open the terminal. The command is copied.") }  // called on the main queue
        }) {
            app.say("Command copied")  // no terminal to open (or you chose "only copy")
            return
        }
        app.say("\(tool.label) started with \(clip(c.name, 24)) attached")
    }

    /// Start with a task: it goes into the first message, so the session gets to work at once.
    func startWithTask(_ c: ContextInfo) {
        guard let app = app else { return }
        guard let task = app.askText("Start a session with \(c.name)", "What should it do? It starts with the context attached and this as its task.", ""),
              !task.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        start(c, task: task)
    }

    func startElsewhere(_ c: ContextInfo, tool: AskEngine? = nil) {
        guard let f = folderPicker() else { return }
        start(c, tool: tool, folder: f)
    }

    func copyReference(_ c: ContextInfo) {
        guard c.built, !c.reference.isEmpty else { app?.say("Build it first"); return }
        app?.copier(c.reference)
        app?.say("Copied: paste it into any Claude or Codex session")
    }

    func openFile(_ c: ContextInfo) {
        guard c.built else { app?.say("Build it first"); return }
        NSWorkspace.shared.open(URL(fileURLWithPath: c.file))
    }

    func showInFinder(_ c: ContextInfo) {
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: c.built ? c.file : c.dir)])
    }

    func delete(_ c: ContextInfo) {
        guard let app = app else { return }
        guard app.destructiveConfirmer("Delete the context “\(c.name)”?",
                                       "Its notes (CONTEXT.md and sessions.md) are deleted. Your \(c.sessions) session\(c.sessions == 1 ? "" : "s") are not touched.") else { return }
        app.act(["context", "rm", c.slug, "--yes"], done: "Deleted \(clip(c.name, 30))")
    }

    /// From a session's menu: put it in a context (it is read on the next Update).
    func add(_ row: Row, to c: ContextInfo) {
        guard let app = app else { return }
        if c.members.contains(row.ref) { app.say("Already in \(clip(c.name, 30))"); return }
        app.act(["context", "add", c.slug, row.ref], done: "Added to \(clip(c.name, 24)): Update reads it")
    }
}
