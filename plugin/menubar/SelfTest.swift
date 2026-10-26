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

    print(failed == 0 ? "selftest passed" : "selftest: \(failed) failed")
    return failed
}
