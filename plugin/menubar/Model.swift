// What the popover shows and does. The views only draw this; `--dump` prints it as text for the tests.
import AppKit
import Combine
import ServiceManagement

enum Tab: String, CaseIterable, Identifiable {
    case sessions, starred, chains
    var id: String { rawValue }
    var title: String {
        switch self {
        case .sessions: return "Sessions"
        case .starred: return "Starred"
        case .chains: return "Chains"
        }
    }
}

/// A titled block of rows. `reviewAll` adds the "Review all N unsaved…" line under Unsaved.
struct RowSection: Identifiable {
    let id: String
    let title: String
    let rows: [Row]
    var reviewAll: Int? = nil
}

/// What was typed in a row's "save with a note" editor.
struct Draft: Equatable {
    var note = ""
    var status = ""
}

enum Layout {
    /// What is shown for a tab. Pure: the same inputs always give the same sections, so tests can print it.
    static func sections(tab: Tab, query: String, data: MenuData, results: [Row]?) -> [RowSection] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        switch tab {
        case .sessions:
            if !q.isEmpty {
                let rows = results ?? data.unsaved.filter { $0.matches(q) } + data.recent.filter { $0.matches(q) }  // until the real search answers
                return [RowSection(id: "results", title: "Results · \(rows.count)", rows: rows)]
            }
            var out: [RowSection] = []
            if !data.unsaved.isEmpty {
                out.append(RowSection(id: "unsaved", title: "Unsaved · \(data.counts.unsaved)", rows: data.unsaved,
                                      reviewAll: data.counts.unsaved > data.unsaved.count ? data.counts.unsaved : nil))
            }
            if !data.recent.isEmpty { out.append(RowSection(id: "recent", title: "Recent", rows: data.recent)) }
            return out
        case .starred:
            if q.isEmpty { return data.starred.isEmpty ? [] : [RowSection(id: "starred", title: "Starred · \(data.starred.count)", rows: data.starred)] }
            let rows = results.map { $0.filter { $0.starred } } ?? data.starred.filter { $0.matches(q) }
            return [RowSection(id: "starred-results", title: "Starred matching · \(rows.count)", rows: rows)]
        case .chains:
            return []
        }
    }

    static func chains(query: String, data: MenuData) -> [ChainInfo] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if q.isEmpty { return data.chains }
        return data.chains.filter { c in
            let hay = [c.id, c.title, c.status ?? ""].joined(separator: " ").lowercased()
            return q.split(whereSeparator: { $0.isWhitespace }).allSatisfy { hay.contains($0) }
        }
    }
}

/// The session under the pointer, shown at once in the preview panel (the system tooltip waits 1-2 seconds).
/// Its own object, so moving over the list redraws only the panel and not every row.
@MainActor
final class HoverModel: ObservableObject {
    @Published private(set) var row: Row?
    func enter(_ r: Row) { row = r }
    /// Leaving a row clears the panel only if it still shows that row (a late "exit" must not wipe the next row).
    func exit(_ r: Row) { if row?.ref == r.ref { row = nil } }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var data: MenuData?
    @Published var problem: String?          // nil = fine (or still loading while data == nil)
    @Published var stale = false             // the last refresh failed; `data` is the last good one
    @Published var tab: Tab = .sessions {  // the search follows the tab; the old tab's results are not shown under the new one
        didSet { if tab != oldValue { results = nil; searchFailed = false; if isSearching { queryChanged() } } }
    }
    @Published var query = "" { didSet { if query != oldValue { queryChanged() } } }
    @Published private(set) var results: [Row]?
    @Published private(set) var searching = false
    @Published private(set) var searchFailed = false
    @Published var editing: String?          // ref of the row whose "save with a note" editor is open
    @Published var drafts: [String: Draft] = [:]  // what was typed there: survives the popover closing
    @Published var chainOpen: [String: Bool] = [:]  // which chains are expanded (the views come and go; this stays)
    @Published private(set) var toast: String?
    @Published var terminal = TerminalChoice.current
    @Published var loginEnabled = false

    let hover = HoverModel()
    private var inFlight = false
    private var refreshAgain = false
    private var lastRaw: Data?
    private var searchTask: Task<Void, Never>?
    private var toastTask: Task<Void, Never>?
    /// How a search is answered (text, only-starred). Tests replace it.
    var finder: @Sendable (String, Bool) -> [Row]? = { CLI.find($0, starredOnly: $1) }
    /// Asked before opening a session chainlog warns about. Tests replace it.
    var confirmer: (String, String) -> Bool = { Alerts.confirm($0, $1, ok: "Open anyway") }
    /// Opens a terminal with a command. Tests replace it.
    var launcher: (_ command: String, _ name: String, _ onFailure: @escaping @Sendable () -> Void) -> Bool = { Launcher.open($0, name: $1, onFailure: $2) }

    // MARK: what is shown

    var sections: [RowSection] {
        guard let d = data else { return [] }
        return Layout.sections(tab: tab, query: query, data: d, results: results)
    }
    var chains: [ChainInfo] { data.map { Layout.chains(query: query, data: $0) } ?? [] }
    /// What Enter opens first: on Chains that is the newest round of the newest chain (the list shows rounds newest first).
    var visibleRows: [Row] { tab == .chains ? chains.flatMap { $0.members.reversed() } : sections.flatMap { $0.rows } }
    var totalChains: Int { data.map { $0.counts.chains ?? $0.chains.count } ?? 0 }
    var moreChains: Int { max(0, totalChains - (data?.chains.count ?? 0)) }
    var unsavedCount: Int { data?.counts.unsaved ?? 0 }
    var isSearching: Bool { !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    func count(for tab: Tab) -> Int? {
        guard let d = data else { return nil }
        switch tab {
        case .sessions: return d.counts.unsaved > 0 ? d.counts.unsaved : nil
        case .starred: return d.counts.starred > 0 ? d.counts.starred : nil
        case .chains: let n = d.counts.chains ?? d.chains.count; return n > 0 ? n : nil
        }
    }

    // MARK: refreshing (one call at a time; a request made meanwhile runs right after)

    func refresh() {
        if inFlight { refreshAgain = true; dbg("refresh queued (one is running)"); return }
        inFlight = true
        dbg("refresh")
        DispatchQueue.global(qos: .utility).async {
            let r = CLI.fetch()
            dbg("fetched: data=\(r.data != nil) problem=\(r.problem ?? "none")")
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.apply(r.data, r.problem, r.raw) }
            }
        }
    }

    func apply(_ new: MenuData?, _ newProblem: String?, _ raw: Data?) {
        inFlight = false
        // A hiccup (chainlog busy, a timeout) must not blank a list that was fine a minute ago.
        let hiccup = new == nil && newProblem == "failed" && data != nil
        stale = hiccup
        if !hiccup {
            data = new
            problem = newProblem
            lastRaw = raw
        }
        loginEnabled = SMAppService.mainApp.status == .enabled
        if let e = editing, let d = data, !(d.unsaved + d.recent + d.starred + d.chains.flatMap { $0.members }).contains(where: { $0.ref == e }) {
            editing = nil
        }
        dbg("state: list=\(data != nil) unsaved=\(data?.counts.unsaved ?? -1) starred=\(data?.counts.starred ?? -1) stale=\(stale) problem=\(problem ?? "none")")
        if refreshAgain { refreshAgain = false; refresh() }
        // a search that was typed before the first answer arrived
        if isSearching && results == nil && !searching { queryChanged() }
    }

    // MARK: search

    func queryChanged() {
        searchTask?.cancel()
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        // the Chains tab filters the chains it already has: no need to ask chainlog
        if q.isEmpty || tab == .chains { results = nil; searching = false; searchFailed = false; return }
        searching = true
        let finder = self.finder
        let starredOnly = tab == .starred
        searchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 220_000_000)  // wait for the typing to pause
            if Task.isCancelled { return }
            let rows = await Task.detached(priority: .userInitiated) { finder(q, starredOnly) }.value
            if Task.isCancelled { return }
            guard let self = self else { return }
            self.results = rows ?? []
            self.searchFailed = rows == nil
            self.searching = false
        }
    }

    /// Tests: answer the current query right now, without the delay or a subprocess.
    func answerSearchNow() {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        searchTask?.cancel()
        guard !q.isEmpty, tab != .chains else { results = nil; searching = false; searchFailed = false; return }
        let rows = finder(q, tab == .starred)
        results = rows ?? []
        searchFailed = rows == nil
        searching = false
    }

    func clearSearch() { query = "" }

    /// The popover closed: the next time starts with an empty search. A note you were typing is kept.
    func popoverClosed() { clearSearch() }

    // MARK: actions

    func say(_ text: String) {
        toast = text
        toastTask?.cancel()
        toastTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            if !Task.isCancelled { self?.toast = nil }
        }
    }

    /// Click = open in the terminal you chose; ⌥-click = copy the command.
    func open(_ row: Row, copyOnly: Bool = false) {
        guard row.canResume else { say("Can't reopen: " + (row.resume.blockers.first ?? "unknown")); return }
        if copyOnly {
            Launcher.copy(row.resume.command)
            say("Command copied")
            return
        }
        // `chainlog resume` asks before it does these; so does the app (a running session would get a second copy)
        if let w = row.warning, !confirmer("Open this session?", w) { return }
        if !launcher(row.resume.command, "resume-" + row.short, {
            MainActor.assumeIsolated { self.say("Could not open the terminal. The command is copied.") }  // called on the main queue
        }) {
            say("Command copied")  // no terminal to open (or you chose "only copy")
        }
    }

    func openFirst() { if let r = visibleRows.first(where: { $0.canResume }) { open(r) } }

    func copyCommand(_ row: Row) { open(row, copyOnly: true) }

    func toggleStar(_ row: Row) {
        let on = !row.starred
        setStarred(on, ref: row.ref, row: row)
        DispatchQueue.global(qos: .userInitiated).async {
            let r = Actions.star(ref: row.ref, on: on)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    if r.ok { self.refresh() } else {
                        self.setStarred(!on, ref: row.ref, row: row)
                        self.say("Could not \(on ? "star" : "unstar"): " + clip(r.message, 80))
                    }
                }
            }
        }
    }

    /// Show a star change at once (and in every place the row appears); the real answer replaces it on refresh.
    func setStarred(_ on: Bool, ref: String, row: Row) {
        func upd(_ rows: [Row]) -> [Row] { rows.map { var r = $0; if r.ref == ref { r.starred = on }; return r } }
        if var d = data {
            d.unsaved = upd(d.unsaved)
            d.recent = upd(d.recent)
            d.chains = d.chains.map { var c = $0; c.members = upd(c.members); return c }
            if on {
                if !d.starred.contains(where: { $0.ref == ref }) { var r = row; r.starred = true; d.starred.insert(r, at: 0) }
                d.starred = upd(d.starred)
            } else {
                d.starred.removeAll { $0.ref == ref }
            }
            d.counts.starred = d.starred.count
            data = d
        }
        if let r = results { results = upd(r) }
    }

    func beginSave(_ row: Row) { editing = row.ref }

    /// Debugging / screenshots: preview the first row as soon as there is one.
    func hoverFirstWhenReady(tries: Int = 0) {
        if let r = visibleRows.first { hover.enter(r); return }
        if tries < 60 { DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in self?.hoverFirstWhenReady(tries: tries + 1) } }
    }

    func draft(for ref: String) -> Draft { drafts[ref] ?? Draft() }
    func setDraft(_ d: Draft, for ref: String) { drafts[ref] = d }

    func isOpen(chain id: String, defaultOpen: Bool) -> Bool { chainOpen[id] ?? defaultOpen }
    func toggle(chain id: String, defaultOpen: Bool) { chainOpen[id] = !isOpen(chain: id, defaultOpen: defaultOpen) }

    /// Debugging / screenshots: open the note editor on the first row as soon as there are rows.
    func editFirstWhenReady(tries: Int = 0) {
        if let r = visibleRows.first { beginSave(r); return }
        if tries < 60 { DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in self?.editFirstWhenReady(tries: tries + 1) } }
    }
    func cancelSave() { if let e = editing { drafts[e] = nil }; editing = nil }

    func save(_ row: Row, note: String, status: String?) {
        let text = note.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty || !(status ?? "").isEmpty else { say("Write a note, or pick a result"); return }
        editing = nil
        drafts[row.ref] = nil
        DispatchQueue.global(qos: .userInitiated).async {
            let r = Actions.save(ref: row.ref, note: text, status: status)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    if !r.ok {  // give the text back: the user should not have to type it again
                        self.drafts[row.ref] = Draft(note: text, status: status ?? "")
                        if self.editing == nil { self.editing = row.ref }  // not if they have started another note meanwhile
                    }
                    self.say(r.ok ? "Saved" : "Could not save: " + clip(r.message, 80) + (self.editing == row.ref ? "" : " (your note is kept: click the pencil)"))
                    self.refresh()
                    if self.isSearching { self.queryChanged() }  // a saved note can change what the search finds
                }
            }
        }
    }

    func runChainlog(_ args: [String], name: String) {
        guard let exe = CLI.locate() else { say("chainlog was not found"); return }
        let cmd = ([shellQuote(exe)] + args.map(shellQuote)).joined(separator: " ")
        if !Launcher.open(cmd, name: name, onFailure: { MainActor.assumeIsolated { self.say("Command copied") } }) { say("Command copied") }
    }

    func reviewAll() { runChainlog(["review"], name: "review") }
    func openChainlog() { runChainlog([], name: "chainlog") }

    func setTerminal(_ c: TerminalChoice) { TerminalChoice.current = c; terminal = TerminalChoice.current }

    func setLogin(_ on: Bool) {
        let svc = SMAppService.mainApp
        do {
            if on { try svc.register() } else { try svc.unregister() }
        } catch {
            say("Could not change \"Open at login\". Move Chainlog.app to ~/Applications.")
        }
        loginEnabled = svc.status == .enabled
    }
}
