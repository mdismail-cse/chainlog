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
        m.beginSave(r); m.setDraft(Draft(note: "half a thought", status: "fail"), for: r.ref)
        m.popoverClosed()
        check("closing the popover keeps the open editor", m.editing == r.ref)
        check("  ... and what was typed", m.draft(for: r.ref) == Draft(note: "half a thought", status: "fail"))
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

    print(failed == 0 ? "selftest passed" : "selftest: \(failed) failed")
    return failed
}
