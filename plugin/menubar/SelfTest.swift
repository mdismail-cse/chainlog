// `Chainlog --selftest sample.json`: checks of the model's behaviour, with no window and no chainlog.
// Each line is one fact about how the popover behaves; a failure exits 1. Run by `build.sh --check` and the tests.
import AppKit

@MainActor
func selfTest(sample path: String?) -> Int {
    var failed = 0
    func check(_ name: String, _ ok: @autoclosure () -> Bool, _ detail: @autoclosure () -> String = "") {
        let good = ok()
        print("\(good ? "ok  " : "FAIL") \(name)\(good ? "" : " — " + detail())")
        if !good { failed += 1 }
    }
    guard let path = path, let raw = FileManager.default.contents(atPath: path),
          let data = try? JSONDecoder().decode(MenuData.self, from: raw) else {
        print("FAIL could not read the sample file"); return 1
    }
    func model() -> AppModel { let m = AppModel(); m.data = data; return m }
    func row(_ fields: String = "") -> Row {
        let json = """
        {"ref":"claude:x","short":"x","harness":"claude","title":"T","saved":"none","status":null,"live":false,"starred":false,
         "chain":null,"round":null,"ago":"1h","last_work":null,
         "resume":{"cwd":"/tmp","command":"echo hi","blockers":[],"notes":[]}\(fields)}
        """
        return try! JSONDecoder().decode(Row.self, from: Data(json.utf8))
    }
    func rowWith(notes: [String], live: Bool = false) -> Row {
        let n = notes.map { "\"\($0)\"" }.joined(separator: ",")
        var r = try! JSONDecoder().decode(Row.self, from: Data("""
        {"ref":"claude:y","short":"y","harness":"claude","title":"Busy","saved":"none","status":null,"live":\(live),"starred":false,
         "chain":null,"round":null,"ago":"now","last_work":null,
         "resume":{"cwd":"/tmp","command":"echo busy","blockers":[],"notes":[\(n)]}}
        """.utf8))
        r.starred = false
        return r
    }

    // Enter on the Chains tab opens the newest round, not round 1 (the list shows rounds newest first)
    do {
        let m = model(); m.tab = .chains
        let chain = data.chains[0]
        check("Chains tab: Enter opens the newest round", m.visibleRows.first?.round == chain.rounds,
              "first is r\(m.visibleRows.first?.round ?? -1), newest is r\(chain.rounds)")
    }

    // a note you are writing survives the popover closing; cancelling throws it away
    do {
        let m = model(); let r = data.unsaved[0]
        m.beginSave(r); m.setDraft(Draft(note: "half a thought", status: "fail", chain: "refund work"), for: r.ref)
        m.popoverClosed()
        check("closing the popover keeps the open editor", m.editing == r.ref)
        check("  ... and what was typed", m.draft(for: r.ref) == Draft(note: "half a thought", status: "fail", chain: "refund work"))
        m.cancelSave()
        check("Cancel drops the editor and the draft", m.editing == nil && m.draft(for: r.ref) == Draft())
        m.query = "abc"; m.popoverClosed()
        check("closing clears the search", m.query == "")
    }

    // which chains are expanded lives in the model, not in a view that scrolls away
    do {
        let m = model()
        check("the newest chain starts open", m.isOpen(chain: "a", defaultOpen: true))
        m.toggle(chain: "a", defaultOpen: true)
        check("collapsing it sticks (the default no longer applies)", !m.isOpen(chain: "a", defaultOpen: true))
        check("another chain is unaffected", !m.isOpen(chain: "b", defaultOpen: false))
        m.toggle(chain: "b", defaultOpen: false)
        check("expanding another sticks", m.isOpen(chain: "b", defaultOpen: false))
    }

    // the search follows the tab: all sessions, only starred, or not at all on Chains
    do {
        let m = model()
        final class Spy: @unchecked Sendable { var calls: [(String, Bool)] = [] }
        let spy = Spy()
        m.finder = { q, starred in spy.calls.append((q, starred)); return [] }
        m.query = "refund"; m.answerSearchNow()
        check("Sessions tab searches everything", spy.calls.last?.1 == false && spy.calls.count == 1)
        m.tab = .starred; m.answerSearchNow()
        check("Starred tab asks for starred results only", spy.calls.last?.1 == true && spy.calls.count == 2)
        m.tab = .chains; m.answerSearchNow()
        check("Chains tab does not ask chainlog at all", spy.calls.count == 2 && m.results == nil)
        check("  ... it filters the chains it has", m.chains.count <= data.chains.count)
    }

    // every session is counted and listed, and a capped list says how many are not shown
    do {
        let m = model()
        check("the Sessions tab counts every session, not just the unsaved ones", m.count(for: .sessions) == data.counts.all)
        let titles = m.sections.map { $0.title }
        check("the Unsaved header says what unsaved covers", titles.first?.contains("last 24 h") == true, "\(titles)")
        var obj = try! JSONSerialization.jsonObject(with: raw) as! [String: Any]
        var counts = obj["counts"] as! [String: Any]; counts["all"] = 500; obj["counts"] = counts
        let capped = try! JSONDecoder().decode(MenuData.self, from: try! JSONSerialization.data(withJSONObject: obj))
        let m2 = AppModel(); m2.data = capped
        let note = m2.sections.last?.note ?? ""
        check("a list that is cut says how many older ones are not shown", note.contains("older ones are not listed"), note)
        check("  ... and a complete one says nothing", m.sections.last?.note == nil)
    }

    // leaving a tab drops its results at once: they are never shown under the wrong tab
    do {
        let m = model()
        let one: Row = { var r = row(); r.starred = true; return r }()
        m.finder = { _, _ in [one] }
        m.tab = .starred; m.query = "x"; m.answerSearchNow()
        check("Starred search has its results", m.results?.count == 1)
        m.tab = .sessions
        check("switching tab clears them at once (not after the new search answers)", m.results == nil)
    }

    // a note editor whose row has left the lists is closed, its text kept for when the pencil is clicked again
    do {
        let m = model()
        m.beginSave(row()); m.setDraft(Draft(note: "kept", status: ""), for: "claude:x")
        m.apply(data, nil, raw)  // a refresh: "claude:x" is not in the sample's lists
        check("a refresh closes an editor whose row is gone", m.editing == nil)
        check("  ... and keeps what was typed", m.draft(for: "claude:x").note == "kept")
        let there = data.unsaved[0]
        m.beginSave(there); m.apply(data, nil, raw)
        check("an editor whose row is still listed stays open", m.editing == there.ref)
    }

    // an older chainlog without the chain total still works
    do {
        var obj = try! JSONSerialization.jsonObject(with: raw) as! [String: Any]
        var counts = obj["counts"] as! [String: Any]
        counts.removeValue(forKey: "chains")
        obj["counts"] = counts
        let older = try! JSONSerialization.data(withJSONObject: obj)
        let d = try? JSONDecoder().decode(MenuData.self, from: older)
        check("a payload without counts.chains still decodes", d != nil)
        let m = AppModel(); m.data = d
        check("  ... the chain total falls back to what is listed", m.totalChains == d?.chains.count && m.moreChains == 0)
    }

    // starred results are the server's, even beyond what is loaded locally
    do {
        let m = model()
        let far: Row = { var r = row(); r.starred = true; return r }()
        m.finder = { _, _ in [far] }
        m.tab = .starred; m.query = "needle"; m.answerSearchNow()
        let shown = m.sections.flatMap { $0.rows }
        check("a starred match that is not in the loaded list still shows", shown.map { $0.ref } == ["claude:x"], "\(shown.map { $0.ref })")
    }

    // one failed refresh keeps the last list; the next good one clears the warning
    do {
        let m = model()
        m.apply(nil, "failed", nil)
        check("a failed refresh keeps the list and says so", m.data != nil && m.stale)
        m.apply(data, nil, raw)
        check("the next good refresh clears it", !m.stale)
        let m2 = AppModel(); m2.apply(nil, "failed", nil)
        check("with no list yet, a failure is shown as a problem", m2.data == nil && m2.problem == "failed")
    }

    // starring shows at once, everywhere the row is, and reverts cleanly
    do {
        let m = model(); let r = data.unsaved[0]
        let before = m.data!.counts.starred
        m.setStarred(true, ref: r.ref, row: r)
        check("star: counted and in the Starred list", m.data!.counts.starred == before + 1 && m.data!.starred.contains { $0.ref == r.ref })
        check("  ... marked where it is listed", m.data!.unsaved.first { $0.ref == r.ref }?.starred == true)
        m.setStarred(false, ref: r.ref, row: r)
        check("unstar puts it all back", m.data!.counts.starred == before && !m.data!.starred.contains { $0.ref == r.ref })
    }

    // opening: chainlog's own warnings are asked about first
    do {
        final class Calls: @unchecked Sendable { var opened: [String] = []; var asked: [String] = [] }
        let calls = Calls()
        let m = model()
        m.launcher = { cmd, _, _ in calls.opened.append(cmd); return true }
        let busy = rowWith(notes: ["this session is still running (pid 1); resuming opens a second copy."], live: true)
        m.confirmer = { _, text in calls.asked.append(text); return false }
        m.open(busy)
        check("a running session asks first", calls.asked.count == 1 && calls.opened.isEmpty)
        m.confirmer = { _, text in calls.asked.append(text); return true }
        m.open(busy)
        check("  ... and opens when you agree", calls.opened == ["echo busy"])
        calls.opened = []; calls.asked = []
        m.open(row())
        check("a plain session opens without asking", calls.opened == ["echo hi"] && calls.asked.isEmpty)
        m.open(busy, copyOnly: true)
        check("copying never asks and never opens", calls.asked.isEmpty && calls.opened == ["echo hi"])
        let blocked = try! JSONDecoder().decode(Row.self, from: Data("""
        {"ref":"claude:z","short":"z","harness":"claude","title":"Gone","saved":"user","status":null,"live":false,"starred":false,
         "chain":null,"round":null,"ago":"3d","last_work":null,
         "resume":{"cwd":"/tmp","command":"echo no","blockers":["the transcript file is gone"],"notes":[]}}
        """.utf8))
        calls.opened = []
        m.open(blocked)
        check("a session that cannot be reopened is not opened", calls.opened.isEmpty && (m.toast ?? "").contains("Can't reopen"))
    }

    // sessions started in the VS Code extension: tagged, found by "vscode", and reopened in the extension
    do {
        final class Ran: @unchecked Sendable { var cmds: [String] = []; var asked: [String] = [] }
        let ran = Ran()
        let m = model()
        m.shell = { cmd, _ in ran.cmds.append(cmd) }
        let vs = data.unsaved.first { $0.via == "VS Code" }
        check("a session started in VS Code is tagged", vs?.detail.contains("VS Code") == true, vs?.detail ?? "none in the sample")
        check("  ... and typing vscode or vs code finds it", vs?.matches("vscode") == true && vs?.matches("vs code") == true)
        check("  ... while a desktop-app session says Desktop", data.unsaved.contains { $0.via == "Desktop" && $0.detail.contains("Desktop") })
        if let vs = vs {
            m.openInVSCode(vs)
            check("Open in VS Code runs the row's own command, without a terminal", ran.cmds == [vs.resume.vscode ?? "-"] && m.toast == "Opening in VS Code…")
            m.shell = { _, fail in fail() }
            m.openInVSCode(vs)
            check("  ... and says so when VS Code cannot be started", (m.toast ?? "").contains("Could not open VS Code"), m.toast ?? "")
        }
        let plain = row()
        check("a terminal session has no VS Code action", plain.resume.vscode == nil && plain.via == nil)
        ran.cmds = []
        m.shell = { cmd, _ in ran.cmds.append(cmd) }
        m.openInVSCode(plain)
        check("  ... and asking anyway runs nothing", ran.cmds.isEmpty && (m.toast ?? "").contains("can't be opened in VS Code"), m.toast ?? "")
        let running = try! JSONDecoder().decode(Row.self, from: Data("""
        {"ref":"claude:v","short":"v","harness":"claude","via":"VS Code","title":"Open in the panel","saved":"none","status":null,"live":true,
         "starred":false,"chain":null,"round":null,"ago":"1m","last_work":null,
         "resume":{"cwd":"/tmp","command":"echo hi","vscode":"echo vscode","blockers":[],"notes":["this session is still running (pid 1)."]}}
        """.utf8))
        m.confirmer = { _, text in ran.asked.append(text); return false }
        m.openInVSCode(running)
        check("a running session asks before it is opened in VS Code too", ran.asked.count == 1 && ran.cmds.isEmpty)
        // output from a chainlog that does not know about VS Code (no `via`, no `resume.vscode`) still reads
        check("an older chainlog's rows (no via, no vscode) still decode", plain.via == nil)
    }

    // copying what reopens a session, to paste into any terminal, VS Code or chat: the clipboard gets exactly one thing each time
    do {
        final class Board: @unchecked Sendable { var got: [String] = [] }
        let board = Board()
        let m = model()
        m.copier = { board.got.append($0) }
        let vs = data.unsaved.first { $0.via == "VS Code" }
        let first = data.recent.first ?? data.unsaved[0]
        m.copyCommand(first)
        check("Copy command puts the row's own command on the clipboard", board.got == [first.resume.command] && m.toast == "Command copied", m.toast ?? "")
        board.got = []
        m.copySessionID(first)
        check("Copy session id puts the id on the clipboard, and nothing else", board.got == [first.resume.session_id ?? "-"] && m.toast == "Session id copied", m.toast ?? "")
        check("  ... and the id is inside the command it reopens", first.resume.session_id.map { first.resume.command.contains($0) } == true)
        if let vs = vs {
            board.got = []
            m.copyLink(vs)
            check("Copy VS Code link puts the vscode:// link on the clipboard", board.got == [vs.resume.link ?? "-"] && (vs.resume.link ?? "").hasPrefix("vscode://anthropic.claude-code/open?session="), board.got.first ?? "nothing")
            check("  ... for the session it names", vs.resume.link.map { $0.hasSuffix(vs.resume.session_id ?? "-") } == true)
        }
        let plain = row()
        board.got = []
        m.copyLink(plain)
        check("a terminal session has no link to copy, and nothing reaches the clipboard", board.got.isEmpty && plain.resume.link == nil && (m.toast ?? "").contains("no VS Code link"), m.toast ?? "")
        let blocked = try! JSONDecoder().decode(Row.self, from: Data("""
        {"ref":"claude:g","short":"g","harness":"claude","title":"Gone","saved":"none","status":null,"live":false,"starred":false,
         "chain":null,"round":null,"ago":"9d","last_work":null,
         "resume":{"cwd":"/tmp","command":"","blockers":["the transcript file is gone"],"notes":[]}}
        """.utf8))
        board.got = []
        m.copyCommand(blocked)
        check("a session that cannot be reopened copies no command", board.got.isEmpty && (m.toast ?? "").contains("Can't reopen"), m.toast ?? "")
        // output from a chainlog that predates the copy fields still decodes, and says why nothing was copied
        let old = try! JSONDecoder().decode(Row.self, from: Data("""
        {"ref":"claude:o","short":"o","harness":"claude","title":"Old chainlog","saved":"none","status":null,"live":false,"starred":false,
         "chain":null,"round":null,"ago":"1m","last_work":null,"resume":{"cwd":"/tmp","command":"echo hi","blockers":[],"notes":[]}}
        """.utf8))
        board.got = []
        m.copySessionID(old)
        check("an older chainlog's rows (no session id) decode, and copy nothing", board.got.isEmpty && old.resume.session_id == nil && (m.toast ?? "").contains("Update chainlog"), m.toast ?? "")
    }

    // keeping copies and deleting: each is one chainlog command, and a deletion is asked about first
    do {
        final class Calls: @unchecked Sendable { var ran: [[String]] = []; var asked: [String] = []; var ok = true }
        let calls = Calls()
        let m = model()
        m.inline = true
        m.runner = { args in calls.ran.append(args); return calls.ok ? (true, "") : (false, "it is still running; close it first") }
        m.destructiveConfirmer = { title, _ in calls.asked.append(title); return true }
        let kept = data.recent.first { $0.isKept }
        check("the sample has a session with a kept copy, tagged in its detail line", kept?.detail.contains("kept") == true, kept?.detail ?? "none")
        let plain = row()
        m.toggleKeep(plain)
        check("Keep a copy runs `chainlog keep REF`", calls.ran == [["keep", plain.ref]] && (m.toast ?? "").contains("Kept"), "\(calls.ran) \(m.toast ?? "")")
        calls.ran = []
        if let kept = kept {
            m.toggleKeep(kept)
            check("Stop keeping runs `chainlog unkeep REF --yes`", calls.ran == [["unkeep", kept.ref, "--yes"]], "\(calls.ran)")
        }
        let allThatIsLeft = try! JSONDecoder().decode(Row.self, from: Data("""
        {"ref":"claude:k","short":"k","harness":"claude","title":"Only the copy is left","saved":"user","status":null,"live":false,"starred":false,"kept":true,
         "chain":null,"round":null,"ago":"40d","last_work":null,
         "resume":{"cwd":"/tmp","command":"chainlog restore claude:k && echo hi","restores":true,"blockers":[],"notes":[]}}
        """.utf8))
        calls.ran = []; calls.asked = []
        m.destructiveConfirmer = { title, _ in calls.asked.append(title); return false }
        m.toggleKeep(allThatIsLeft)
        check("dropping the only copy of a session asks first, and No runs nothing", calls.asked.count == 1 && calls.ran.isEmpty)
        calls.ran = []; calls.asked = []
        m.destructiveConfirmer = { title, _ in calls.asked.append(title); return true }
        for scope in ["both", "claude", "chainlog"] { m.delete(plain, scope: scope) }
        check("Delete runs `chainlog delete REF --from SCOPE --yes` for each scope, asking each time",
              calls.ran == ["both", "claude", "chainlog"].map { ["delete", plain.ref, "--from", $0, "--yes"] } && calls.asked.count == 3, "\(calls.ran)")
        calls.ran = []; calls.asked = []
        m.destructiveConfirmer = { _, _ in calls.asked.append("asked"); return false }
        m.delete(plain, scope: "both")
        check("saying No to a deletion runs nothing", calls.ran.isEmpty && calls.asked.count == 1)
        m.destructiveConfirmer = { _, _ in true }
        calls.ok = false
        m.delete(plain, scope: "both")
        check("a refused deletion says why", (m.toast ?? "").contains("Could not delete") && (m.toast ?? "").contains("still running"), m.toast ?? "")
        calls.ran = []
        let codex = try! JSONDecoder().decode(Row.self, from: Data("""
        {"ref":"codex:c","short":"c","harness":"codex","title":"A Codex session","saved":"none","status":null,"live":false,"starred":false,
         "chain":null,"round":null,"ago":"1h","last_work":null,
         "resume":{"cwd":"/tmp","command":"codex resume c","blockers":[],"notes":[]}}
        """.utf8))
        m.toggleKeep(codex)
        check("Codex sessions are not copied (Codex does not delete them)", calls.ran.isEmpty)
        check("a session whose original Claude deleted but a copy exists can still be opened", allThatIsLeft.restores && allThatIsLeft.canResume)
    }

    // the gear menu: how long Claude keeps sessions (asked, checked, then changed) and the automatic-keep switch
    do {
        final class Calls: @unchecked Sendable { var ran: [[String]] = []; var asked: [String] = []; var typed: String? = "120"; var plan = "{\"current\":90,\"to\":120,\"changed\":true,\"would_delete\":0,\"would_delete_unkept\":0}" }
        let calls = Calls()
        let m = model()
        m.inline = true
        m.runner = { args in calls.ran.append(args); return args.contains("--json") ? (true, calls.plan) : (true, "") }
        m.askText = { _, _, initial in calls.asked.append(initial); return calls.typed }
        m.shortenConfirmer = { _, _ in calls.asked.append("shorten?"); return false }
        check("the gear menu shows how long Claude keeps sessions and what is about to go",
              data.retention?.title == "Claude keeps sessions for 90 days…" && data.retention?.warning == "2 expire within 14 days (1 without a kept copy)",
              data.retention?.title ?? "none")
        func info(_ expiring: Int, _ unkept: Int) -> RetentionInfo {
            let json = "{\"days\":90,\"is_default\":false,\"expiring\":\(expiring),\"expiring_unkept\":\(unkept),\"expiring_within\":14}"
            return try! JSONDecoder().decode(RetentionInfo.self, from: Data(json.utf8))
        }
        check("no warning when everything about to expire is kept, and one session is worded as one",
              info(3, 0).warning == nil && info(0, 0).warning == nil && info(1, 1).warning == "1 expires within 14 days (1 without a kept copy)",
              "\(info(3, 0).warning ?? "nil") | \(info(1, 1).warning ?? "nil")")
        m.askRetention()
        check("changing it asks (starting from today's number), checks the plan, then writes",
              calls.asked == ["90"] && calls.ran == [["retention", "120", "--json"], ["retention", "120", "--yes"]] && (m.toast ?? "").contains("120 days"),
              "\(calls.asked) \(calls.ran) \(m.toast ?? "")")
        calls.ran = []; calls.asked = []; calls.typed = "30"
        calls.plan = "{\"current\":90,\"to\":30,\"changed\":true,\"would_delete\":4,\"would_delete_unkept\":3}"
        m.askRetention()
        check("shortening it warns what Claude would delete, and No writes nothing",
              calls.asked == ["90", "shorten?"] && calls.ran == [["retention", "30", "--json"]], "\(calls.asked) \(calls.ran)")
        calls.ran = []; calls.asked = []
        for bad in ["0", "abc", "99999", ""] {
            calls.typed = bad
            m.askRetention()
        }
        check("a number that is not 1 to 3650 is refused before anything runs", calls.ran.isEmpty && (m.toast ?? "").contains("1 to 3650"), m.toast ?? "")
        calls.ran = []; calls.typed = nil
        m.askRetention()
        check("cancelling runs nothing", calls.ran.isEmpty)
        calls.typed = "90"
        m.askRetention()
        check("the number it already is changes nothing", calls.ran.isEmpty && (m.toast ?? "").contains("Already 90"))
        m.toggleKeepAuto()
        check("the switch for automatic keeping runs `chainlog config set keep.auto false` (it is on)",
              calls.ran == [["config", "set", "keep.auto", "false"]], "\(calls.ran)")
    }

    // search text that looks like a command-line option is passed as text
    do {
        check("search text is one argument with `=` (so \"--json\" is text)", CLI.findArgument("--json") == "--find=--json")
    }

    // where the popover opens, on a built-in display with the menu bar and an external one to its right without one
    do {
        let builtin = NSRect(x: 0, y: 0, width: 1728, height: 1117), builtinVisible = NSRect(x: 0, y: 0, width: 1728, height: 1084)
        let external = NSRect(x: 1728, y: 37, width: 1920, height: 1080)  // no menu bar: visible == frame
        let frames = [builtin, external]
        func place(_ p: NSPoint) -> (Int, NSPoint) {
            let i = Placement.screenIndex(for: p, frames: frames)
            return (i, Placement.anchor(click: p, visible: i == 0 ? builtinVisible : external, frame: frames[i]))
        }
        // a click in the built-in menu bar, near its right end
        let a = place(NSPoint(x: 1650, y: 1100))
        check("built-in display: opens on it, under the menu bar", a.0 == 0 && a.1.y == 1084 && a.1.x == 1650, "\(a)")
        // the icon on the external display sits in a menu bar ABOVE the display's top edge: not inside any display
        let b = place(NSPoint(x: 3540, y: 1149))
        check("external display (click above its top edge): opens on the external display", b.0 == 1, "\(b)")
        check("  ... just under its top, at the click's x (the popover slides to stay on the display)", b.1.y == 1117 && b.1.x == 3540, "\(b)")
        let c = place(NSPoint(x: 2700, y: 1140))
        check("external display, middle: right under the click", c.0 == 1 && c.1.x == 2700 && c.1.y == 1117, "\(c)")
        let d = place(NSPoint(x: 1730, y: 1117))
        check("external display, left end: at the click, inside the display", d.0 == 1 && d.1.x == 1732, "\(d)")
        let e = place(NSPoint(x: 900, y: 1500))
        check("a click far above everything still picks the nearest display", e.0 == 0)
        let lone = Placement.anchor(click: NSPoint(x: 10, y: 10), visible: NSRect(x: 0, y: 0, width: 300, height: 300), frame: NSRect(x: 0, y: 0, width: 300, height: 300))
        check("an anchor is never placed off its display", lone.x == 10 && Placement.anchor(click: NSPoint(x: -50, y: 0), visible: NSRect(x: 0, y: 0, width: 300, height: 300), frame: NSRect(x: 0, y: 0, width: 300, height: 300)).x == 4)
    }

    // the click that closed the popover must not reopen it (a transient popover closes on the mouse down)
    do {
        let t0 = Date(timeIntervalSince1970: 1_000_000)
        var g = ToggleGuard()
        check("a popover that was never closed can be opened", g.shouldOpen(at: t0))
        g.closed(at: t0)
        check("the click that closes it (its mouse up 0.15 s later) does not reopen it", !g.shouldOpen(at: t0.addingTimeInterval(0.15)))
        check("  ... even a slow click (0.5 s)", !g.shouldOpen(at: t0.addingTimeInterval(0.5)))
        check("a deliberate click a moment later opens it again", g.shouldOpen(at: t0.addingTimeInterval(0.9)))
    }

    // reaching for a menu bar that hides itself must not close the popover (macOS hands focus back to the app underneath)
    do {
        let frames = [NSRect(x: 0, y: 0, width: 1728, height: 1117), NSRect(x: 1728, y: 37, width: 1920, height: 1080)]
        check("the pointer on the very top edge of a display is at the menu bar", Dismissal.atMenuBar(NSPoint(x: 2600, y: 1117), frames: frames))
        check("  ... on the built-in display's menu bar", Dismissal.atMenuBar(NSPoint(x: 900, y: 1100), frames: frames))
        check("  ... just above a display's top edge (the bar that sits above it)", Dismissal.atMenuBar(NSPoint(x: 3000, y: 1140), frames: frames))
        check("the pointer in the middle of a display is not", !Dismissal.atMenuBar(NSPoint(x: 900, y: 600), frames: frames))
        check("  ... nor on the popover's own list, a screenful below the top", !Dismissal.atMenuBar(NSPoint(x: 2600, y: 700), frames: frames))
        check("  ... nor at the bottom edge", !Dismissal.atMenuBar(NSPoint(x: 900, y: 0), frames: frames))
        check("  ... nor off to the side of every display", !Dismissal.atMenuBar(NSPoint(x: 5000, y: 1117), frames: frames))
    }

    // hover: the preview follows the pointer, and a late "exit" never wipes the row you moved onto
    do {
        let m = model()
        let r1 = data.unsaved[0], r2 = data.recent[0]
        m.hover.enter(r1); m.hover.enter(r2); m.hover.exit(r1)
        check("leaving the old row after entering the new one keeps the new preview", m.hover.row?.ref == r2.ref)
        m.hover.exit(r2)
        check("leaving the row clears the preview", m.hover.row == nil)
        check("a row's folder is shown with ~ for the home folder", row().folder == "/tmp")
    }

    // Ask: chatting with Claude or Codex about your sessions
    do {
        final class Calls: @unchecked Sendable {
            var requests: [AskRequest] = []
            var onEvent: [(AskEvent) -> Void] = []
            var onDone: [(Int32) -> Void] = []
            var cancelled = 0
            var statusReads = 0
            var ran: [[String]] = []
            var asked: [(String, String)] = []
            var opened: [String] = []
            var copied: [String] = []
        }
        let calls = Calls()
        func ev(_ json: String) -> AskEvent { try! JSONDecoder().decode(AskEvent.self, from: Data(json.utf8)) }
        let connectedJSON = #"{"engine":"claude","connected":true}"#
        func chatModel(connected: Bool = true) -> AppModel {
            var d = data
            d.ask = try! JSONDecoder().decode(AskConfig.self, from: Data((connected ? connectedJSON : #"{"engine":null,"connected":false}"#).utf8))
            let m = AppModel(); m.data = d
            m.copier = { calls.copied.append($0) }
            m.launcher = { cmd, _, _ in calls.opened.append(cmd); return true }
            m.runner = { args in calls.ran.append(args); return (true, "ok") }
            m.chat.inline = true
            m.chat.starter = { req, onEvent, onDone in
                calls.requests.append(req); calls.onEvent.append(onEvent); calls.onDone.append(onDone)
                return { calls.cancelled += 1 }
            }
            m.chat.statusReader = { calls.statusReads += 1; return try? JSONDecoder().decode(AskStatus.self, from: Data(statusJSON.utf8)) }
            m.chat.connectConfirmer = { t, d in calls.asked.append((t, d)); return true }
            m.chat.disconnectConfirmer = { t, d in calls.asked.append((t, d)); return true }
            return m
        }
        let statusJSON = #"""
        {"engine":null,"consented":[],"model":null,"ready":false,
         "engines":{"claude":{"installed":true,"signed_in":true,"detail":"signed in"},
                    "codex":{"installed":false,"signed_in":null,"detail":"not installed (npm install -g @openai/codex)"}},
         "disclosure":{"claude":"Text is sent to Claude. Secrets are redacted.","codex":"Text is sent to Codex."}}
        """#
        let shown = data.recent[0]
        func rowJSON(_ r: Row) -> String {  // a row as `chainlog _ask` sends it (only what the app reads)
            let o: [String: Any] = ["ref": r.ref, "short": r.short, "harness": r.harness, "title": r.title, "saved": r.saved, "status": NSNull(),
                                    "live": false, "starred": false, "chain": NSNull(), "round": NSNull(), "ago": r.ago, "last_work": NSNull(),
                                    "resume": ["cwd": r.resume.cwd, "command": r.resume.command, "blockers": [String](), "notes": [String]()] as [String: Any]]
            return String(data: try! JSONSerialization.data(withJSONObject: o), encoding: .utf8)!
        }
        let answerJSON = """
        {"type":"answer","answer":"It ended in a failure. [[\(shown.short)]] and [[ffffffff]] **bold**","engine":"claude","model":"sonnet","calls":2,
         "sessions":[\(rowJSON(shown))]}
        """

        // not connected: nothing is sent, and it says why
        var m = chatModel(connected: false)
        m.chat.send("hello")
        check("Ask: not connected, nothing is asked", calls.requests.isEmpty && m.chat.messages.isEmpty && m.toast == "Connect Claude or Codex first", m.toast ?? "")
        check("Ask: the tab knows it is not connected", !m.chat.connected && m.chat.supported)
        var old = data; old.ask = nil
        let oldModel = AppModel(); oldModel.data = old
        check("Ask: a chainlog without the Ask tab is recognised", !oldModel.chat.supported && !oldModel.chat.connected)
        check("Ask: the tab has no count and no rows", m.count(for: .ask) == nil && Tab.allCases.last == .ask && Layout.sections(tab: .ask, query: "", data: data, results: nil).isEmpty)

        // a question: sent once, history empty, shown as thinking until it answers
        m = chatModel()
        check("Ask: connected, the engine is the one chainlog reports", m.chat.connected && m.chat.engine == .claude)
        m.chat.draft = "  what did I do?  "
        m.chat.send()
        check("Ask: a question is sent trimmed, and the box is emptied", calls.requests.count == 1 && calls.requests[0].question == "what did I do?" && m.chat.draft == "")
        check("  ... with no earlier messages, and the tool chosen by chainlog", calls.requests[0].history.isEmpty && calls.requests[0].engine == nil)
        check("  ... it shows as the user's message and as thinking", m.chat.messages.map { $0.role } == [.user] && m.chat.thinking && m.chat.progress == "Starting…")
        m.chat.send("a second question while waiting")
        m.chat.draft = "   "
        m.chat.send()
        check("Ask: nothing is sent while waiting, or for an empty box", calls.requests.count == 1 && m.chat.messages.count == 1)
        calls.onEvent[0](ev(#"{"type":"status","text":"Asking Claude…"}"#))
        check("Ask: its progress is shown", m.chat.progress == "Asking Claude…")
        calls.onEvent[0](ev(answerJSON))
        calls.onDone[0](0)
        let reply = m.chat.messages.last
        check("Ask: the answer arrives with the sessions it mentions and who answered", reply?.role == .assistant && reply?.sessions.map { $0.short } == [shown.short] && reply?.meta == "Claude · sonnet · 2 requests", reply?.meta ?? "-")
        check("  ... and it is no longer thinking", !m.chat.thinking && m.chat.messages.count == 2)

        // the next question carries the conversation, without errors, and at most the last eight messages
        calls.onEvent.removeAll(); calls.onDone.removeAll()
        m.chat.send("and the second round?")
        check("Ask: the next question carries what was said before", calls.requests.last?.history.map { $0["role"] ?? "" } == ["user", "assistant"] && calls.requests.last?.history.first?["text"] == "what did I do?")
        calls.onEvent[0](ev(#"{"type":"error","code":"timeout","message":"Claude took too long."}"#))
        calls.onDone[0](0)
        check("Ask: an error is shown as a message and the chat goes on", m.chat.messages.last?.role == .error && m.chat.messages.last?.code == "timeout" && !m.chat.thinking)
        check("  ... and a plain failure does not send you back to the connect screen", calls.statusReads == 0)
        for i in 0..<6 { m.chat.send("q\(i)"); calls.onEvent.last?(ev(#"{"type":"answer","answer":"a","engine":"claude","calls":1,"sessions":[]}"#)); calls.onDone.last?(0) }
        let hist = m.chat.historyPayload()
        check("Ask: at most the last eight messages are carried, and never an error", hist.count == 8 && hist.allSatisfy { $0["role"] == "user" || $0["role"] == "assistant" })
        calls.onEvent.removeAll(); calls.onDone.removeAll()
        m.chat.send("this one fails")
        calls.onEvent[0](ev(#"{"type":"error","code":"failed","message":"Claude could not answer: boom"}"#)); calls.onDone[0](0)
        let afterError = m.chat.historyPayload()
        check("Ask: an error just shown is not sent back as part of the conversation", !afterError.contains { ($0["text"] ?? "").contains("boom") } && afterError.last?["text"] == "this one fails", "\(afterError.suffix(2))")

        // signed out / not connected any more: back to the connect screen's information
        calls.onEvent.removeAll(); calls.onDone.removeAll()
        m.chat.send("one more")
        calls.onEvent[0](ev(#"{"type":"error","code":"not_signed_in","message":"Claude says you are not signed in."}"#))
        calls.onDone[0](0)
        check("Ask: being signed out re-reads what is installed and signed in", calls.statusReads == 1 && m.chat.status?.engines["claude"]?.usable == true)

        // chainlog ends without saying anything
        calls.onEvent.removeAll(); calls.onDone.removeAll()
        m.chat.send("silent?")
        calls.onDone[0](3)
        check("Ask: chainlog ending without an answer is reported, not waited on", !m.chat.thinking && m.chat.messages.last?.code == "failed" && (m.chat.messages.last?.text ?? "").contains("exit 3"), m.chat.messages.last?.text ?? "")

        // Stop
        calls.onEvent.removeAll(); calls.onDone.removeAll(); calls.cancelled = 0
        m.chat.send("long one")
        let before = m.chat.messages.count
        m.chat.stop()
        check("Ask: Stop ends the request and says so", calls.cancelled == 1 && !m.chat.thinking && m.chat.messages.last?.code == "stopped" && m.chat.messages.count == before + 1)
        calls.onEvent[0](ev(answerJSON)); calls.onDone[0](0)
        check("  ... and whatever it still prints is ignored", m.chat.messages.count == before + 1 && !m.chat.thinking)
        m.chat.stop()
        check("  ... stopping again does nothing", calls.cancelled == 1 && m.chat.messages.count == before + 1)
        // a request that is far too slow
        calls.onEvent.removeAll(); calls.onDone.removeAll(); calls.cancelled = 0
        m.chat.send("slow")
        m.chat.timedOut()
        check("Ask: a request that never ends is stopped and reported", calls.cancelled == 1 && !m.chat.thinking && m.chat.messages.last?.code == "timeout")
        // New chat
        m.chat.send("pending")
        m.chat.clear()
        check("Ask: a new chat empties the conversation (and stops a pending question)", m.chat.messages.isEmpty && !m.chat.thinking && calls.cancelled == 2)
        // chainlog missing
        m.chat.starter = { _, _, _ in nil }
        m.chat.send("hello?")
        check("Ask: chainlog not being found is a message, not a hang", !m.chat.thinking && m.chat.messages.last?.code == "not_found")
        // a suggestion sends itself and leaves what you typed
        m.chat.clear(); m.chat.draft = "half typed"
        m.chat.starter = { req, onEvent, onDone in calls.requests.append(req); return { } }
        m.chat.send(ChatModel.suggestions[0])
        check("Ask: a suggestion is sent as it is and does not touch the box", calls.requests.last?.question == ChatModel.suggestions[0] && m.chat.draft == "half typed")

        // connecting: the details are shown first, and only a yes connects
        m = chatModel(connected: false)
        m.chat.loadStatus()
        check("Ask: the connect screen reads what is installed and signed in", m.chat.status?.engines["claude"]?.usable == true && m.chat.status?.engines["codex"]?.usable == false && !m.chat.checking)
        calls.ran = []; calls.asked = []
        m.chat.connect(.claude)
        check("Ask: connecting shows what is sent first", calls.asked.count == 1 && calls.asked[0].0 == "Connect Claude?" && calls.asked[0].1 == "Text is sent to Claude. Secrets are redacted.", calls.asked.first?.1 ?? "-")
        check("  ... then runs one chainlog command", calls.ran == [["ask", "--connect", "claude", "--yes"]] && m.chat.connecting == nil, "\(calls.ran)")
        calls.ran = []; calls.asked = []
        m.chat.connectConfirmer = { t, d in calls.asked.append((t, d)); return false }
        m.chat.connect(.claude)
        check("Ask: saying no connects nothing", calls.ran.isEmpty && calls.asked.count == 1 && m.chat.connecting == nil)
        calls.ran = []; calls.asked = []
        m.chat.connectConfirmer = { t, d in calls.asked.append((t, d)); return true }
        let agreed = #"{"engine":"claude","consented":["claude","codex"],"model":"sonnet","ready":true,"engines":{"claude":{"installed":true,"signed_in":true,"detail":"signed in"},"codex":{"installed":true,"signed_in":true,"detail":"signed in"}},"disclosure":{"claude":"c","codex":"x"}}"#
        m.chat.statusReader = { try? JSONDecoder().decode(AskStatus.self, from: Data(agreed.utf8)) }
        m.chat.loadStatus()
        m.chat.connect(.codex)
        check("Ask: switching to a tool you already agreed to does not ask again", calls.asked.isEmpty && calls.ran == [["ask", "--connect", "codex", "--yes"]], "\(calls.ran)")
        m = chatModel()
        calls.ran = []; calls.asked = []
        m.chat.disconnectConfirmer = { t, d in calls.asked.append((t, d)); return false }
        m.chat.disconnect()
        check("Ask: Disconnect asks, and no changes nothing", calls.asked.count == 1 && calls.ran.isEmpty)
        m.chat.disconnectConfirmer = { _, _ in true }
        m.chat.send("something"); m.chat.stop()
        m.chat.disconnect()
        check("Ask: Disconnect runs one chainlog command and clears the chat", calls.ran == [["ask", "--disconnect"]] && m.chat.messages.isEmpty, "\(calls.ran)")

        // links in an answer
        m = chatModel()
        m.chat.send("q")
        calls.onEvent.last?(ev(answerJSON)); calls.onDone.last?(0)
        let link = URL(string: "\(AskText.scheme)://s/\(shown.short)")!
        calls.opened = []
        check("Ask: a link to a session in the answer opens that session", m.chat.open(link) && calls.opened == [shown.resume.command], "\(calls.opened)")
        check("  ... a link to some other session opens nothing", m.chat.open(URL(string: "\(AskText.scheme)://s/ffffffff")!) && calls.opened.count == 1)
        check("  ... and any other link is not ours to open", !m.chat.open(URL(string: "https://evil.example/login")!) && !m.chat.open(URL(string: "file:///etc/passwd")!) && calls.opened.count == 1)
        m.chat.copy(m.chat.messages.last!)
        check("Ask: Copy puts the answer on the clipboard with session names, not ids", calls.copied.last?.contains("\(clip(shown.title, 40)) (\(shown.short))") == true && calls.copied.last?.contains("[[") == false && m.toast == "Answer copied", calls.copied.last ?? "-")
    }

    // the text of an answer: only sessions that came with it become links, whatever the AI wrote
    do {
        let a = data.recent[0], b = data.recent[1]
        let text = "See [[\(a.short)]] then [[\(b.short.uppercased())]], not [[abcdef12]]. [phish](https://evil.example/x) and ![i](https://evil.example/i.png) and **bold**."
        let attr = AskText.attributed(text, sessions: [a, b])
        let links = attr.runs.compactMap { $0.link }
        check("answer: each mentioned session is one link", links.count == 2 && links.allSatisfy { AskText.session(in: $0) != nil }, "\(links)")
        check("  ... named after the session", String(attr.characters).contains(clip(a.title, 30)) && String(attr.characters).contains(clip(b.title, 30)))
        check("  ... an unknown id disappears and no other link survives", !String(attr.characters).contains("[[") && !links.contains { $0.scheme != AskText.scheme })
        check("  ... **bold** is kept as bold text", attr.runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true && String(attr[$0.range].characters) == "bold" })
        check("  ... a link's text stays as plain text", String(attr.characters).contains("phish"))
        let oddTitle = "*b* [x](https://evil.example) `c` <b>"
        let odd = AskText.attributed("[[\(a.short)]]", sessions: [try! JSONDecoder().decode(Row.self, from: Data(#"{"ref":"claude:z","short":"\#(a.short)","harness":"claude","title":"*b* [x](https://evil.example) `c` <b>","saved":"none","status":null,"live":false,"starred":false,"chain":null,"round":null,"ago":"1h","last_work":null,"resume":{"cwd":"/tmp","command":"x","blockers":[],"notes":[]}}"#.utf8))])
        check("answer: a title full of markdown is shown as typed, and is still one link", odd.runs.compactMap { $0.link }.count == 1 && String(odd.characters) == clip(oddTitle, 30), String(odd.characters))
        check("  ... with no bold, italics or code made out of it", !odd.runs.contains { $0.inlinePresentationIntent != nil }, "\(odd.runs.map { String(describing: $0.inlinePresentationIntent) })")
        check("answer: plain text names sessions and drops unknown ids", AskText.plain("[[\(a.short)]] [[abcdef12]]", sessions: [a]) == "\(clip(a.title, 40)) (\(a.short)) ")
        check("answer: a link is recognised only in our own shape", AskText.session(in: URL(string: "chainlog-session://s/abc")!) == "abc" && AskText.session(in: URL(string: "chainlog-session://x/abc")!) == nil && AskText.session(in: URL(string: "https://s/abc")!) == nil)
    }

    // reading what `chainlog _ask` prints, which arrives in pieces
    do {
        let lines = AskLines()
        var got = lines.feed(Data(#"{"type":"status","te"#.utf8))
        check("lines: half a line gives nothing yet", got.isEmpty)
        got = lines.feed(Data("xt\":\"a\"}\n{\"type\":\"status\",\"text\":\"b\"}\nnot json\n{\"type\":\"ans".utf8))
        check("lines: the rest completes it, and a bad line is skipped", got.map { $0.text ?? "" } == ["a", "b"], "\(got.map { $0.text ?? "" })")
        got = lines.feed(Data("wer\",\"answer\":\"x\"}".utf8))
        check("lines: a last line without its newline waits for the end", got.isEmpty && lines.finish().first?.answer == "x")
        check("lines: nothing is left after the end", lines.finish().isEmpty)
        let req = AskRequest(question: "what's \"in\" a\nname 😀?", history: [["role": "user", "text": "hi"]], engine: "codex")
        let obj = try? JSONSerialization.jsonObject(with: req.json) as? [String: Any]
        check("request: the question, history and engine travel as JSON, whatever they hold", (obj?["question"] as? String) == "what's \"in\" a\nname 😀?" && (obj?["engine"] as? String) == "codex" && (obj?["history"] as? [[String: String]])?.count == 1)
        check("request: no engine is left out, not null", (try? JSONSerialization.jsonObject(with: AskRequest(question: "q", history: [], engine: nil).json) as? [String: Any])?["engine"] == nil)
    }

    print(failed == 0 ? "selftest passed" : "selftest: \(failed) failed")
    return failed
}
