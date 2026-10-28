// The popover. Search at the top, tabs under it, the list, then a footer. All behaviour is in AppModel.
import AppKit
import SwiftUI

struct PopoverView: View {
    @ObservedObject var model: AppModel
    var close: () -> Void = {}
    @FocusState private var searchFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            SearchBar(model: model, focused: $searchFocused, close: close)
            TabBar(model: model)
            Divider()
            ZStack(alignment: .bottom) {
                content
                if let t = model.toast {
                    Text(t)
                        .font(.system(size: 12, weight: .medium))
                        .padding(.horizontal, 12).padding(.vertical, 7)
                        .background(Capsule().fill(Color.primary.opacity(0.85)))
                        .foregroundColor(Color(nsColor: .windowBackgroundColor))
                        .padding(.bottom, 10)
                        .transition(.opacity)
                }
            }
            Divider()
            PreviewPanel(hover: model.hover)
            Divider()
            Footer(model: model)
        }
        .frame(width: 440, height: 560)
        .onAppear { DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { searchFocused = true } }
    }

    @ViewBuilder var content: some View {
        if model.data == nil {
            ProblemView(problem: model.problem)
        } else if model.tab == .chains {
            ChainsList(model: model)
        } else {
            SessionList(model: model)
        }
    }
}

// MARK: - search and tabs

struct SearchBar: View {
    @ObservedObject var model: AppModel
    var focused: FocusState<Bool>.Binding
    var close: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundColor(.secondary)
            TextField(model.tab == .chains ? "Search chains, rounds, results…" : "Search all sessions, notes, PR numbers…", text: $model.query)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
                .focused(focused)
                .onSubmit { model.openFirst() }
                .onExitCommand { if model.query.isEmpty { close() } else { model.clearSearch() } }
            if model.searching { ProgressView().controlSize(.small).scaleEffect(0.7) }
            if !model.query.isEmpty {
                Button { model.clearSearch() } label: { Image(systemName: "xmark.circle.fill").foregroundColor(.secondary) }
                    .buttonStyle(.plain).help("Clear the search (Esc)")
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.07)))
        .padding(.horizontal, 12).padding(.top, 12).padding(.bottom, 8)
    }
}

struct TabBar: View {
    @ObservedObject var model: AppModel

    var body: some View {
        HStack(spacing: 4) {
            ForEach(Tab.allCases) { t in
                let selected = model.tab == t
                Button { model.tab = t } label: {
                    HStack(spacing: 5) {
                        if t == .starred { Image(systemName: "star.fill").font(.system(size: 10)).foregroundColor(.yellow) }
                        Text(t.title).font(.system(size: 12.5, weight: selected ? .semibold : .regular))
                        if let n = model.count(for: t) {
                            Text("\(n)").font(.system(size: 10.5, weight: .medium))
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(Capsule().fill(Color.primary.opacity(selected ? 0.16 : 0.09)))
                        }
                    }
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(RoundedRectangle(cornerRadius: 6).fill(selected ? Color.primary.opacity(0.1) : .clear))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            Spacer()
        }
        .padding(.horizontal, 10).padding(.bottom, 8)
    }
}

// MARK: - lists

struct SessionList: View {
    @ObservedObject var model: AppModel

    var body: some View {
        let sections = model.sections
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(sections) { section in
                    SectionHeader(text: section.title)
                    ForEach(section.rows) { row in SessionRow(model: model, row: row) }
                    if let note = section.note {
                        Text(note).font(.system(size: 11.5)).foregroundColor(.secondary).padding(.horizontal, 14).padding(.vertical, 6)
                    }
                    if let n = section.reviewAll {
                        Button { model.reviewAll() } label: {
                            Text("Review all \(n) unsaved…").font(.system(size: 12.5)).foregroundColor(.accentColor)
                        }
                        .buttonStyle(.plain).padding(.horizontal, 14).padding(.vertical, 6)
                    }
                }
                if sections.allSatisfy({ $0.rows.isEmpty }) { EmptyState(model: model) }
            }
            .padding(.vertical, 4)
        }
    }
}

struct ChainsList: View {
    @ObservedObject var model: AppModel

    var body: some View {
        let chains = model.chains
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                if !chains.isEmpty { SectionHeader(text: model.chainsHeader) }
                ForEach(Array(chains.enumerated()), id: \.element.id) { i, c in ChainBlock(model: model, chain: c, defaultOpen: i == 0) }
                if let footer = model.chainsFooter {
                    Button { model.runChainlog(["chain"], name: "chains") } label: {
                        Text(footer).font(.system(size: 12.5)).foregroundColor(.accentColor)
                    }
                    .buttonStyle(.plain).padding(.horizontal, 14).padding(.vertical, 6)
                }
                if chains.isEmpty { EmptyState(model: model) }
            }
            .padding(.vertical, 4)
        }
    }
}

struct ChainBlock: View {
    @ObservedObject var model: AppModel
    let chain: ChainInfo
    var defaultOpen = false  // the newest chain starts expanded
    var open: Bool { model.isOpen(chain: chain.id, defaultOpen: defaultOpen) }  // kept in the model: rows scroll in and out of view

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { withAnimation(.easeOut(duration: 0.12)) { model.toggle(chain: chain.id, defaultOpen: defaultOpen) } } label: {
                HStack(spacing: 8) {
                    Image(systemName: open ? "chevron.down" : "chevron.right").font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.secondary).frame(width: 14)
                    Text(chain.label).font(.system(size: 13, weight: .semibold))
                    if let s = chain.status { StatusPill(text: s) }
                    Spacer()
                    Text("\(chain.rounds) round\(chain.rounds == 1 ? "" : "s") · \(chain.ago)")
                        .font(.system(size: 11.5)).foregroundColor(.secondary)
                }
                .padding(.horizontal, 12).padding(.vertical, 8).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                if let latest = chain.continuable {
                    Button { model.open(latest) } label: {
                        Label("Continue the latest round (r\(chain.rounds))", systemImage: "arrow.uturn.forward")
                            .font(.system(size: 12.5)).foregroundColor(.accentColor)
                    }
                    .buttonStyle(.plain).padding(.leading, 34).padding(.vertical, 4)
                }
                ForEach(chain.members.reversed()) { m in SessionRow(model: model, row: m, round: m.round).padding(.leading, 14) }
            }
        }
    }
}

struct SectionHeader: View {
    let text: String
    var body: some View {
        Text(text).font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
            .padding(.horizontal, 14).padding(.top, 10).padding(.bottom, 3)
    }
}

struct StatusPill: View {
    let text: String
    var body: some View {
        let color: Color = ["pass": .green, "fail": .red, "blocked": .red, "wip": .orange][text] ?? .secondary
        Text(clip(text, 12)).font(.system(size: 10.5, weight: .semibold)).foregroundColor(color)
            .padding(.horizontal, 6).padding(.vertical, 1)
            .background(Capsule().fill(color.opacity(0.14)))
    }
}

struct EmptyState: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 26)).foregroundColor(.secondary.opacity(0.7))
            Text(title).font(.system(size: 13, weight: .semibold))
            Text(detail).font(.system(size: 12)).foregroundColor(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity).padding(.top, 70).padding(.horizontal, 30)
    }

    var q: String { model.query.trimmingCharacters(in: .whitespacesAndNewlines) }
    var icon: String { !q.isEmpty ? "magnifyingglass" : (model.tab == .starred ? "star" : (model.tab == .chains ? "link" : "tray")) }
    var title: String {
        if !q.isEmpty {
            let what = model.tab == .chains ? "chain" : "session"
            return model.searching ? "Searching…" : (model.searchFailed ? "Search is not available" : "No \(what) matches “\(clip(q, 24))”")
        }
        switch model.tab {
        case .starred: return "Nothing starred yet"
        case .chains: return "No PR chains yet"
        case .sessions: return "No sessions yet"
        }
    }
    var detail: String {
        if !q.isEmpty {
            if model.searchFailed { return "Update chainlog and try again." }
            if model.tab == .chains {  // the Chains tab searches the chains it was sent, and the newest 30 are all it is sent
                return model.moreChains > 0
                    ? "Only the newest \(model.data?.chains.count ?? 30) chains are loaded here. “All \(model.totalChains) chains in Terminal…” below reaches the rest."
                    : "Searches chain names and the titles, results and tools of their rounds."
            }
            return "Searches titles, your notes, PR numbers, branches and folders."
        }
        switch model.tab {
        case .starred: return "Hover a session and click the star to keep it here."
        case .chains: return "Save sessions that mention a PR (say “PR 4321”) and chainlog groups the rounds."
        case .sessions: return "Use Claude Code or Codex as usual and they show up here."
        }
    }
}

struct ProblemView: View {
    let problem: String?
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: problem == nil ? "hourglass" : "exclamationmark.triangle").font(.system(size: 26)).foregroundColor(.secondary)
            Text(title).font(.system(size: 13, weight: .semibold))
            Text(detail).font(.system(size: 12)).foregroundColor(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity).padding(30)
    }
    var title: String {
        switch problem {
        case nil: return "Loading…"
        case "notfound": return "chainlog was not found"
        case "newer": return "chainlog is newer than this app"
        default: return "Could not read chainlog's answer"
        }
    }
    var detail: String {
        switch problem {
        case nil: return "Asking chainlog for your sessions."
        case "notfound": return "Install it from github.com/mdismail-cse/chainlog, then reopen this."
        case "newer": return "Run: chainlog setup menubar"
        default: return "Update chainlog, or run: chainlog doctor"
        }
    }
}

// MARK: - one session

struct SessionRow: View {
    @ObservedObject var model: AppModel
    let row: Row
    var round: Int? = nil
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: icon.symbol).font(.system(size: 11.5)).foregroundColor(icon.color).frame(width: 16)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        if let r = round { Text("r\(r)").font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary) }
                        Text(row.title).font(.system(size: 13, weight: .medium)).lineLimit(1)
                        if let s = row.statusLabel { StatusPill(text: s) }
                    }
                    Text(row.detail).font(.system(size: 11.5)).foregroundColor(.secondary).lineLimit(1)
                }
                Spacer(minLength: 4)
                if hovering || row.starred || model.editing == row.ref {
                    if hovering || model.editing == row.ref {
                        // paste it into any terminal, VS Code or a chat (⌥-click on the row and the right-click menu do the same)
                        if row.canResume {
                            IconButton(symbol: "doc.on.doc", help: "Copy the command that reopens it (right-click for the session id)") { model.copyCommand(row) }
                        }
                        if row.resume.link != nil {
                            IconButton(symbol: "link", help: "Copy the VS Code link to this session") { model.copyLink(row) }
                        }
                        IconButton(symbol: "square.and.pencil", help: "Save with a note…") { model.beginSave(row) }
                    }
                    IconButton(symbol: row.starred ? "star.fill" : "star", tint: row.starred ? .yellow : .secondary,
                               help: row.starred ? "Remove from Starred" : "Add to Starred") { model.toggleStar(row) }
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 7).fill(hovering ? Color.primary.opacity(0.08) : .clear))
            .opacity(row.canResume ? 1 : 0.55)
            .contentShape(Rectangle())
            .onTapGesture { model.open(row, copyOnly: NSEvent.modifierFlags.contains(.option)) }
            .onHover { inside in
                hovering = inside
                if inside { model.hover.enter(row) } else { model.hover.exit(row) }  // the preview panel shows it at once
            }
            .contextMenu {
                Button("Open") { model.open(row) }.disabled(!row.canResume)
                if row.resume.vscode != nil { Button("Open in VS Code") { model.openInVSCode(row) }.disabled(!row.canResume) }
                Button("Copy command") { model.copyCommand(row) }
                Button("Copy session id") { model.copySessionID(row) }
                if row.resume.link != nil { Button("Copy VS Code link") { model.copyLink(row) } }
                Divider()
                Button(row.starred ? "Remove from Starred" : "Add to Starred") { model.toggleStar(row) }
                Button("Save with a note…") { model.beginSave(row) }
                if row.harness == "claude" {
                    Button(row.isKept ? "Stop keeping the copy" : "Keep a copy (Claude can't delete it)") { model.toggleKeep(row) }
                        .disabled(!row.isKept && !row.canResume)
                }
                Menu("Delete…") {
                    if row.harness == "claude" {
                        Button("From Claude and chainlog") { model.delete(row, scope: "both") }
                        Button("Only from Claude (keeps chainlog's copy)") { model.delete(row, scope: "claude") }
                    }
                    Button("Only from chainlog (keeps Claude's file)") { model.delete(row, scope: "chainlog") }
                }
            }
            if model.editing == row.ref { NoteEditor(model: model, row: row) }
        }
        .padding(.horizontal, 4)
    }

    var icon: (symbol: String, color: Color) {
        if row.live { return ("play.fill", .green) }
        switch row.saved {
        case "user": return ("circle.fill", .accentColor)
        case "auto": return ("circle.lefthalf.filled", .accentColor)
        default: return ("circle", .secondary)
        }
    }

}

/// What the hovered session is about: its title, the last thing said in it, and where it ran. Instant, at a fixed
/// height so the list never jumps.
struct PreviewPanel: View {
    @ObservedObject var hover: HoverModel

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            if let r = hover.row {
                Text(r.title).font(.system(size: 12.5, weight: .semibold)).lineLimit(2)
                Text(r.last_work ?? "Nothing said in this session yet.")
                    .font(.system(size: 12)).foregroundColor(.secondary).lineLimit(3)
                if let why = r.resume.blockers.first {
                    Text("Can't reopen: " + why).font(.system(size: 11)).foregroundColor(.orange).lineLimit(1)
                } else if r.restores {
                    Text("Claude deleted the original · opens from your kept copy").font(.system(size: 11)).foregroundColor(.accentColor).lineLimit(1)
                } else {
                    Text(r.folder).font(.system(size: 11)).foregroundColor(.secondary.opacity(0.8)).lineLimit(1).truncationMode(.middle)
                }
            } else {
                Text("Hover a session to preview it").font(.system(size: 12)).foregroundColor(.secondary)
                Text("Click to reopen it · hover for Copy · right-click for more").font(.system(size: 11)).foregroundColor(.secondary.opacity(0.8))
            }
        }
        .frame(maxWidth: .infinity, minHeight: 72, maxHeight: 72, alignment: .topLeading)
        .padding(.horizontal, 14).padding(.vertical, 8)
    }
}

struct IconButton: View {
    let symbol: String
    var tint: Color = .secondary
    let help: String
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 12.5)).foregroundColor(tint).frame(width: 22, height: 22).contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(help)
    }
}

/// "Save with a note…": the same as `chainlog save --id … -s … -- note`.
struct NoteEditor: View {
    @ObservedObject var model: AppModel
    let row: Row
    @FocusState private var focused: Bool
    // the text lives in the model, so closing the popover does not throw away what you were writing
    private var note: Binding<String> { Binding(get: { model.draft(for: row.ref).note }, set: { var d = model.draft(for: row.ref); d.note = $0; model.setDraft(d, for: row.ref) }) }
    private var status: Binding<String> { Binding(get: { model.draft(for: row.ref).status }, set: { var d = model.draft(for: row.ref); d.status = $0; model.setDraft(d, for: row.ref) }) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("What happened? (e.g. “refund works, coupon gives 500”)", text: note, axis: .vertical)
                .textFieldStyle(.roundedBorder).lineLimit(1...3).focused($focused)
                .onSubmit { model.save(row, note: note.wrappedValue, status: status.wrappedValue.isEmpty ? nil : status.wrappedValue) }
            HStack(spacing: 8) {
                Picker("", selection: status) {
                    Text("No result").tag("")
                    Text("Pass").tag("pass"); Text("Fail").tag("fail"); Text("Work in progress").tag("wip"); Text("Blocked").tag("blocked")
                }
                .labelsHidden().frame(width: 150)
                Spacer()
                Button("Cancel") { model.cancelSave() }.keyboardShortcut(.cancelAction)
                Button("Save") { model.save(row, note: note.wrappedValue, status: status.wrappedValue.isEmpty ? nil : status.wrappedValue) }
                    .keyboardShortcut(.defaultAction)
                    .disabled(note.wrappedValue.trimmingCharacters(in: .whitespaces).isEmpty && status.wrappedValue.isEmpty)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
        .onAppear { DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { focused = true } }  // after the search box has taken its turn
    }
}

// MARK: - footer

struct Footer: View {
    @ObservedObject var model: AppModel

    var body: some View {
        HStack(spacing: 10) {
            Button { model.openChainlog() } label: {
                Label("Open chainlog in Terminal", systemImage: "terminal").font(.system(size: 12))
            }.buttonStyle(.plain).foregroundColor(.secondary)
            if model.stale {
                Text("Could not refresh just now").font(.system(size: 11)).foregroundColor(.orange)
            }
            Spacer()
            Menu {
                Section("Open sessions in") {
                    ForEach(TerminalChoice.allCases, id: \.self) { c in
                        Button {
                            model.setTerminal(c)
                        } label: {
                            if model.terminal == c { Label(c.label + (c.available ? "" : " (not installed)"), systemImage: "checkmark") }
                            else { Text(c.label + (c.available ? "" : " (not installed)")) }
                        }.disabled(!c.available)
                    }
                }
                Divider()
                Toggle("Open at login", isOn: Binding(get: { model.loginEnabled }, set: { model.setLogin($0) }))
                Toggle("Keep a copy when I save or star", isOn: Binding(get: { model.data?.keep_auto ?? true }, set: { _ in model.toggleKeepAuto() }))
                if let r = model.data?.retention {
                    Button(r.title) { model.askRetention() }
                    if let w = r.warning { Text("⚠︎ " + w) }
                }
                Button("Refresh now") { model.refresh() }
                Divider()
                Text("Click a session to reopen it · ⌥-click copies its command")
                Divider()
                Button("Quit Chainlog") { NSApplication.shared.terminate(nil) }
            } label: {
                Image(systemName: "gearshape").font(.system(size: 13))
            }
            .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
        }
        .padding(.horizontal, 14).padding(.vertical, 9)
    }
}
