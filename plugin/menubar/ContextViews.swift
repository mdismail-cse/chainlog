// The contexts in the Chains tab: the list (start a session with one, copy its reference, update it), and the screens that
// make one (pick sessions and questions, watch it build, see the result). All behaviour is in ContextModel.
import AppKit
import SwiftUI

/// The top of the Chains tab: your contexts, or one line that says what a context is.
struct ContextsSection: View {
    @ObservedObject var model: AppModel
    @ObservedObject var cx: ContextModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(cx.contexts.isEmpty ? "CONTEXTS" : "CONTEXTS · \(cx.contexts.count)")
                    .font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
                Spacer()
                Button { cx.beginNew() } label: { Label("New context", systemImage: "plus").font(.system(size: 12)) }
                    .buttonStyle(.plain).foregroundColor(.accentColor).help("Merge what several sessions taught you into one context")
            }
            .padding(.horizontal, 14).padding(.top, 8).padding(.bottom, 4)
            if cx.contexts.isEmpty {
                Text("A context merges what several sessions taught you (all your cloud backup work, say) into one file. A new session starts with a few lines that point to it, and reads it only when it needs it.")
                    .font(.system(size: 11.5)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 14).padding(.bottom, 6)
            }
            ForEach(cx.contexts) { c in ContextRow(model: model, cx: cx, c: c) }
        }
    }
}

struct ContextRow: View {
    @ObservedObject var model: AppModel
    @ObservedObject var cx: ContextModel
    let c: ContextInfo
    @State private var hovering = false

    var open: Bool { cx.isOpen(c) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: open ? "chevron.down" : "chevron.right").font(.system(size: 10, weight: .semibold))
                    .foregroundColor(.secondary).frame(width: 14)
                Image(systemName: "square.stack.3d.up.fill").font(.system(size: 11.5)).foregroundColor(.accentColor)
                Text(c.name).font(.system(size: 13, weight: .semibold)).lineLimit(1)
                if !c.built { StatusPill(text: "not built") } else if c.needs_update { StatusPill(text: c.pendingShort) }
                Spacer(minLength: 4)
                if hovering && c.built && !open {
                    IconButton(symbol: "play.circle", help: "Start a new \(c.tool.label) session with this context") { cx.start(c) }
                }
                Text("\(c.sessions) session\(c.sessions == 1 ? "" : "s")\(c.built ? " · " + c.ago : "")")
                    .font(.system(size: 11.5)).foregroundColor(.secondary)
            }
            .padding(.horizontal, 12).padding(.vertical, 7)
            .background(RoundedRectangle(cornerRadius: 7).fill(hovering ? Color.primary.opacity(0.06) : .clear))
            .contentShape(Rectangle())
            .onTapGesture { withAnimation(.easeOut(duration: 0.12)) { cx.toggleOpen(c) } }
            .onHover { hovering = $0 }
            .contextMenu { ContextMenuItems(cx: cx, c: c) }
            if open { details }
        }
        .padding(.horizontal, 4)
    }

    @ViewBuilder var details: some View {
        VStack(alignment: .leading, spacing: 8) {
            if c.built {
                if !c.gist.isEmpty {
                    Text(c.gist).font(.system(size: 12)).foregroundColor(.primary.opacity(0.85)).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                }
                Text([c.period, c.engine.flatMap { AskEngine(rawValue: $0)?.label }.map { "built with \($0)" }, c.chain.map { "follows \(clip($0, 24))" }]
                    .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(.system(size: 11)).foregroundColor(.secondary)
                HStack(spacing: 8) {
                    Menu {
                        Button("Start in \(c.tool.label)") { cx.start(c) }
                        Button("Start in \(c.tool.other.label)") { cx.start(c, tool: c.tool.other) }
                        Divider()
                        Button("With a task…") { cx.startWithTask(c) }
                        Button("In another folder…") { cx.startElsewhere(c) }
                    } label: {
                        Label("Start in \(c.tool.label)", systemImage: "play.fill")
                    } primaryAction: { cx.start(c) }
                    .menuStyle(.borderlessButton).fixedSize().tint(.accentColor)
                    .help("A new session that starts with a few lines pointing to this context\(c.start.folder.map { ", in \(($0 as NSString).lastPathComponent)" } ?? "")")
                    Button { cx.copyReference(c) } label: { Label("Copy reference", systemImage: "doc.on.doc").font(.system(size: 12)) }
                        .buttonStyle(.plain).foregroundColor(.accentColor).help("Lines to paste into a session that is already running")
                    Spacer()
                    if c.needs_update {
                        Button("Update") { cx.update(c) }.help("Read \(c.pending.text) and merge again")
                    }
                }
            } else {
                Text("Not built yet: nothing has been read. Build it to write its notes.").font(.system(size: 11.5)).foregroundColor(.secondary)
                HStack {
                    Button("Build") { cx.update(c) }
                    Spacer()
                }
            }
        }
        .padding(.leading, 38).padding(.trailing, 14).padding(.bottom, 8)
    }
}

/// The right-click menu of a context (also its ⋯ button).
struct ContextMenuItems: View {
    @ObservedObject var cx: ContextModel
    let c: ContextInfo
    var body: some View {
        if c.built {
            Button("Start in \(c.tool.label)") { cx.start(c) }
            Button("Start in \(c.tool.other.label)") { cx.start(c, tool: c.tool.other) }
            Button("Start with a task…") { cx.startWithTask(c) }
            Button("Copy reference") { cx.copyReference(c) }
            Divider()
        }
        Button("Add sessions…") { cx.beginAdd(to: c) }
        Button("Edit questions…") { cx.beginEditQuestions(c) }
        Button(c.built ? "Update" : "Build") { cx.update(c) }
        if c.built { Button("Open the notes") { cx.openFile(c) } }
        Button("Show in Finder") { cx.showInFinder(c) }
        Divider()
        Button("Delete…") { cx.delete(c) }
    }
}

/// Where the search box is while a context is being made: what you are doing, and the way back.
struct ContextHeader: View {
    @ObservedObject var cx: ContextModel
    var title: String {
        switch cx.screen {
        case .builder: return cx.target.map { "Add to \(clip($0.name, 26))" } ?? "New context"
        case .building: return "Building \(clip(cx.building, 26))"
        case .done: return cx.result.map { clip($0.name, 30) } ?? "Ready"
        case .failed: return "Not built"
        case .list: return ""
        }
    }
    var body: some View {
        HStack(spacing: 8) {
            if cx.screen != .building {
                Button { cx.screen == .done ? cx.doneWithResult() : cx.closeBuilder() } label: { Image(systemName: "chevron.left") }
                    .buttonStyle(.plain).foregroundColor(.secondary).help("Back to the list")
            }
            Image(systemName: "square.stack.3d.up.fill").foregroundColor(.accentColor)
            Text(title).font(.system(size: 14, weight: .semibold)).lineLimit(1)
            Spacer()
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(height: 40)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.07)))
        .padding(.horizontal, 12).padding(.top, 12).padding(.bottom, 8)
    }
}

/// Instead of the Chains list while a context is being made.
struct ContextScreen: View {
    @ObservedObject var model: AppModel
    @ObservedObject var cx: ContextModel
    var body: some View {
        switch cx.screen {
        case .builder: ContextBuilder(model: model, cx: cx)
        case .building: ContextProgress(cx: cx)
        case .done: ContextDone(cx: cx)
        case .failed: ContextFailed(cx: cx)
        case .list: EmptyView()
        }
    }
}

struct ContextBuilder: View {
    @ObservedObject var model: AppModel
    @ObservedObject var cx: ContextModel
    @FocusState private var nameFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if cx.target == nil {
                        TextField("Name, e.g. Cloud backup", text: $cx.name)
                            .textFieldStyle(.roundedBorder).font(.system(size: 13)).focused($nameFocused)
                    }
                    sessions
                    Divider().padding(.vertical, 2)
                    questions
                }
                .padding(.horizontal, 14).padding(.vertical, 8)
            }
            Divider()
            footer
        }
        .onAppear { if cx.target == nil && cx.name.isEmpty { DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { nameFocused = true } } }
    }

    var sessions: some View {
        VStack(alignment: .leading, spacing: 6) {
            StepTitle(number: 1, title: cx.target == nil ? "Sessions" : "Sessions to add",
                      note: "\(cx.target == nil ? cx.selected.count : cx.adding.count) picked") {
                if !cx.selected.isEmpty { Button("Clear") { cx.clearSelection() }.buttonStyle(.plain).font(.system(size: 11.5)).foregroundColor(.accentColor) }
            }
            Picker("", selection: $cx.pickMode) {
                Text("Describe it (AI)").tag(ContextModel.PickMode.describe)
                Text("Search words").tag(ContextModel.PickMode.words)
            }
            .pickerStyle(.segmented).labelsHidden()
            if cx.pickMode == .describe { describe } else { words }
        }
    }

    /// Describe the work; the AI recommends the sessions about it, grouped by chain or PR.
    @ViewBuilder var describe: some View {
        if !cx.askConnected {
            VStack(alignment: .leading, spacing: 6) {
                Text("The AI that reads your descriptions is the one you connect in the Ask tab. It is sent the same short summaries (titles, notes, PR numbers), not whole sessions.")
                    .font(.system(size: 11.5)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                Button("Connect in the Ask tab") { model.tab = .ask }.font(.system(size: 12))
            }
            .padding(.vertical, 4)
        } else {
            HStack(alignment: .top, spacing: 6) {
                TextField("What are you looking for? e.g. PRs where large backup restores failed or timed out", text: $cx.describeText, axis: .vertical)
                    .textFieldStyle(.roundedBorder).font(.system(size: 12.5)).lineLimit(1...3)
                    .onSubmit { cx.findByDescription() }
                Button(cx.suggesting ? "Finding…" : "Find") { cx.findByDescription() }
                    .disabled(cx.suggesting || cx.describeText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if cx.suggesting {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small).scaleEffect(0.7)
                    Text("Asking \(cx.engine.label) which sessions are about that (1 request, short summaries only)…").font(.system(size: 11)).foregroundColor(.secondary)
                }
            } else if !cx.suggestNote.isEmpty {
                Text(cx.suggestNote).font(.system(size: 11)).foregroundColor(cx.suggestion == nil ? .orange : .secondary).fixedSize(horizontal: false, vertical: true)
            }
            if let s = cx.suggestion, !s.picks.isEmpty {
                HStack(spacing: 12) {
                    let strong = s.picks.filter(\.strong).count
                    if strong > 0 { Button("Pick strong (\(strong))") { cx.pickSuggested(strongOnly: true) } }
                    Button("Pick all (\(s.picks.count))") { cx.pickSuggested(strongOnly: false) }
                    Spacer()
                }
                .buttonStyle(.plain).font(.system(size: 11.5)).foregroundColor(.accentColor)
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(s.groups, id: \.name) { g in
                            HStack {
                                Text(g.name.isEmpty ? "Not in a chain or PR" : g.name).font(.system(size: 11, weight: .semibold)).foregroundColor(.secondary)
                                Spacer()
                                Button("Pick \(g.picks.count)") { cx.pickGroup(g.picks) }.buttonStyle(.plain).font(.system(size: 11)).foregroundColor(.accentColor)
                            }
                            .padding(.horizontal, 6).padding(.top, 4)
                            ForEach(g.picks) { p in PickRow(cx: cx, row: p.row, fit: p.fit, why: p.why) }
                        }
                    }
                }
                .frame(height: 170)
                .background(RoundedRectangle(cornerRadius: 6).stroke(Color.primary.opacity(0.08)))
            } else if !cx.selected.isEmpty {
                pickedBox
            }
        }
    }

    /// The word search, and the newest sessions when nothing is typed.
    @ViewBuilder var words: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass").foregroundColor(.secondary).font(.system(size: 11.5))
            TextField("Find sessions, e.g. backup", text: $cx.pickQuery).textFieldStyle(.plain).font(.system(size: 12.5))
            if cx.pickSearching { ProgressView().controlSize(.small).scaleEffect(0.6) }
            if let r = cx.pickResults, !r.isEmpty {
                let left = r.filter { !cx.isSelected($0) && !cx.isMember($0) }.count
                Button("Pick all \(r.count)") { cx.selectAll(r) }.buttonStyle(.plain).font(.system(size: 11.5)).foregroundColor(.accentColor)
                    .disabled(left == 0)
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(0.06)))
        let rows = cx.candidates
        // a box of its own, so the questions below stay in sight however many sessions there are
        ScrollView {
            VStack(spacing: 0) {
                if rows.isEmpty {
                    Text(cx.pickQuery.isEmpty ? "No sessions yet" : (cx.pickSearching ? "Searching…" : "No session matches"))
                        .font(.system(size: 11.5)).foregroundColor(.secondary).padding(.vertical, 6).frame(maxWidth: .infinity, alignment: .leading)
                }
                ForEach(rows) { r in PickRow(cx: cx, row: r) }
            }
        }
        .frame(height: 150)
        .background(RoundedRectangle(cornerRadius: 6).stroke(Color.primary.opacity(0.08)))
    }

    /// What you picked so far (shown in Describe mode before a search, or after switching from Search words).
    var pickedBox: some View {
        ScrollView {
            VStack(spacing: 0) { ForEach(cx.selected) { r in PickRow(cx: cx, row: r) } }
        }
        .frame(height: 110)
        .background(RoundedRectangle(cornerRadius: 6).stroke(Color.primary.opacity(0.08)))
    }

    var questions: some View {
        VStack(alignment: .leading, spacing: 6) {
            StepTitle(number: 2, title: "Questions for each session", note: "\(cx.cleanQuestions.count)") { EmptyView() }
            HStack(spacing: 4) {
                Text("Start from").font(.system(size: 11.5)).foregroundColor(.secondary)
                Menu {
                    ForEach(cx.presets, id: \.id) { p in Button(p.label) { cx.applyPreset(p) } }
                    if !cx.defaultQuestions.isEmpty { Divider(); Button("Your default") { cx.useDefaultQuestions() } }
                    if cx.target != nil { Button("This context's") { cx.resetQuestions() } }
                } label: { Text(cx.questionsSource).font(.system(size: 11.5, weight: .medium)) }
                .menuStyle(.borderlessButton).menuIndicator(.visible).fixedSize()
                .help("Ready-made sets: a bug fix, a feature, PR review / QA, an investigation")
                Spacer()
            }
            Text("Each one is answered for every session, in up to 80 words. Fewer, sharper questions make shorter notes.")
                .font(.system(size: 11)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
            if cx.questionsChanged, let t = cx.target {
                Text("Changed: all \(t.sessions + cx.adding.count) sessions are read again with these.").font(.system(size: 11)).foregroundColor(.orange)
            }
            ForEach(Array(cx.questions.enumerated()), id: \.offset) { i, q in
                HStack(spacing: 6) {
                    Text("\(i + 1).").font(.system(size: 11.5)).foregroundColor(.secondary).frame(width: 18, alignment: .trailing)
                    TextField("A question", text: Binding(get: { q }, set: { cx.setQuestion(i, $0) }))
                        .textFieldStyle(.roundedBorder).font(.system(size: 12))
                    IconButton(symbol: "minus.circle", help: "Remove this question") { cx.removeQuestion(i) }
                }
            }
            HStack(spacing: 12) {
                Button("Add a question") { cx.addQuestion() }.disabled(cx.questions.count >= 15)
                Spacer()
            }
            .buttonStyle(.plain).font(.system(size: 11.5)).foregroundColor(.accentColor)
            Toggle("Use these questions for new contexts too", isOn: $cx.saveAsDefault).font(.system(size: 11.5)).toggleStyle(.checkbox)
        }
    }

    var footer: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                StepNumber(number: 3)
                Text(cx.blocker ?? cx.estimate).font(.system(size: 11)).foregroundColor(cx.blocker == nil ? .secondary : .orange)
            }
            HStack {
                Text("Each answer cites its session. Secrets are removed first.").font(.system(size: 10.5)).foregroundColor(.secondary.opacity(0.8))
                Spacer()
                Button("Cancel") { cx.closeBuilder() }.keyboardShortcut(.cancelAction)
                Button(cx.target == nil ? "Build" : "Update") { cx.build() }.keyboardShortcut(.defaultAction).disabled(cx.blocker != nil)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
    }
}

/// "① Sessions · 3 picked" with something on the right.
struct StepTitle<Trailing: View>: View {
    let number: Int
    let title: String
    var note: String = ""
    @ViewBuilder var trailing: () -> Trailing
    var body: some View {
        HStack(spacing: 6) {
            StepNumber(number: number)
            Text(title).font(.system(size: 12, weight: .semibold))
            if !note.isEmpty { Text("· " + note).font(.system(size: 11.5)).foregroundColor(.secondary) }
            Spacer()
            trailing()
        }
    }
}

struct StepNumber: View {
    let number: Int
    var body: some View {
        Text("\(number)").font(.system(size: 10, weight: .bold)).foregroundColor(.white)
            .frame(width: 16, height: 16).background(Circle().fill(Color.accentColor))
    }
}

/// One session in the picker: a checkbox, its title, and when.
struct PickRow: View {
    @ObservedObject var cx: ContextModel
    let row: Row
    var fit: String? = nil   // the AI's verdict, when it recommended this one
    var why: String? = nil
    @State private var hovering = false

    var body: some View {
        let member = cx.isMember(row)
        let on = member || cx.isSelected(row)
        HStack(spacing: 8) {
            Image(systemName: on ? "checkmark.square.fill" : "square").foregroundColor(on ? (member ? .secondary : .accentColor) : .secondary)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 5) {
                    Text(row.title).font(.system(size: 12.5)).lineLimit(1)
                    if let f = fit { StatusPill(text: f) }
                }
                if let w = why, !w.isEmpty {
                    Text(w).font(.system(size: 10.5)).foregroundColor(.primary.opacity(0.7)).lineLimit(2)
                }
                Text(member ? "already in it · \(row.ago)" : row.detail).font(.system(size: 10.5)).foregroundColor(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 6).padding(.vertical, 4)
        .background(RoundedRectangle(cornerRadius: 6).fill(hovering ? Color.primary.opacity(0.06) : .clear))
        .contentShape(Rectangle())
        .opacity(member ? 0.6 : 1)
        .onTapGesture { cx.toggle(row) }
        .onHover { hovering = $0 }
    }
}

struct ContextProgress: View {
    @ObservedObject var cx: ContextModel
    var body: some View {
        VStack(spacing: 12) {
            Spacer()
            if cx.progressTotal > 0 {
                ProgressView(value: Double(cx.progressDone), total: Double(max(cx.progressTotal, 1))).frame(width: 300)
            } else {
                ProgressView().controlSize(.small)
            }
            Text(cx.progressText).font(.system(size: 12.5)).foregroundColor(.secondary).multilineTextAlignment(.center)
                .lineLimit(2).frame(width: 340)
            Text("One request per session, then one to merge. It goes on if you close this; what is read is kept if you stop.")
                .font(.system(size: 11)).foregroundColor(.secondary.opacity(0.8)).multilineTextAlignment(.center).frame(width: 330)
            Button("Stop") { cx.stop() }
            Spacer()
        }
        .frame(maxWidth: .infinity)
    }
}

struct ContextDone: View {
    @ObservedObject var cx: ContextModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let c = cx.result {
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.circle.fill").foregroundColor(.green).font(.system(size: 18))
                    Text(cx.resultUpToDate ? "\(c.name) was already up to date" : "\(c.name) is ready").font(.system(size: 14, weight: .semibold))
                }
                Text("\(c.sessions) session\(c.sessions == 1 ? "" : "s") · \(c.period)\(cx.resultUpToDate ? "" : " · \(cx.resultRequests) request\(cx.resultRequests == 1 ? "" : "s")")")
                    .font(.system(size: 11.5)).foregroundColor(.secondary)
                if !c.gist.isEmpty {
                    Text(c.gist).font(.system(size: 12.5)).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                }
                Text("A new session gets only a few lines that point to it, and reads the part it needs when its task calls for it.")
                    .font(.system(size: 11)).foregroundColor(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 10) {
                    Button { cx.start(c); cx.doneWithResult() } label: { Label("Start in \(c.tool.label)", systemImage: "play.fill") }
                        .keyboardShortcut(.defaultAction)
                    Button("Copy reference") { cx.copyReference(c) }
                    Button("Open the notes") { cx.openFile(c) }
                    Spacer()
                    Button("Done") { cx.doneWithResult() }
                }
            }
            Spacer()
        }
        .padding(16)
    }
}

struct ContextFailed: View {
    @ObservedObject var cx: ContextModel
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
                Text("It was not built").font(.system(size: 14, weight: .semibold))
            }
            Text(cx.failure).font(.system(size: 12)).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            Text("What was read before this is kept, so trying again costs only what is left.").font(.system(size: 11)).foregroundColor(.secondary)
            HStack {
                Button("Try again") { cx.retry() }
                Button("Back") { cx.doneWithResult() }
                Spacer()
            }
            Spacer()
        }
        .padding(16)
    }
}
