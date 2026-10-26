// Chainlog: a Mac menu bar app for chainlog. Click the icon: a search box, and your sessions in three tabs
// (Sessions, Starred, Chains). Click a session to reopen it in a terminal; star it; save it with a note.
//
// It holds no data of its own. It runs `chainlog _menu` (JSON, schema chainlog.menu/1), draws the lists, and
// every button runs one `chainlog` command (star, unstar, save) or the resume command chainlog gives it.
// Build: `sh build.sh`.   Headless switches, used by the tests (no window unless stated):
//
//   --dump [FILE] [--tab sessions|starred|chains] [--query TEXT] [--find-file FILE]
//                                       print what the popover shows, as text. FILE defaults to `chainlog _menu`
//   --dump-problem NAME                 what it shows when chainlog can't be read (notfound|newer|failed|loading)
//   --render OUT.png [--dark] [same options as --dump]   draw the popover to a PNG (needs a window server)
//   --print-script COMMAND              the script a click would run in a terminal
//   --launch NAME COMMAND               do what a click does (opens a terminal window)
//   --act star|unstar REF | save REF STATUS|- NOTE      do what a button does, print ok/failed
//   --edit-first                        (with --render or --open-on-launch) open the "save with a note" editor on the first row
//   --open-on-launch                    show the popover once the first answer is in, and keep it open
import AppKit
import Combine
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let popover = NSPopover()
    let model = AppModel()
    var cancellables = Set<AnyCancellable>()
    let pinned = CommandLine.arguments.contains("--open-on-launch") || ProcessInfo.processInfo.environment["CHAINLOG_MENUBAR_PIN"] != nil

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.setActivationPolicy(.accessory)
        popover.behavior = pinned ? .applicationDefined : .transient  // transient: closes when you click elsewhere
        popover.animates = false
        popover.delegate = self
        popover.contentSize = NSSize(width: 440, height: 560)
        popover.contentViewController = NSHostingController(rootView: PopoverView(model: model, close: { [weak self] in
            self?.popover.performClose(nil)
        }))
        if let b = statusItem.button {
            b.image = NSImage(systemSymbolName: "link", accessibilityDescription: "chainlog")
            b.imagePosition = .imageLeading
            b.target = self
            b.action = #selector(toggle(_:))
        }
        model.$data.sink { [weak self] d in
            MainActor.assumeIsolated { self?.updateBadge(d?.counts.unsaved ?? 0) }
        }.store(in: &cancellables)
        if let t = arg("--tab", in: CommandLine.arguments), let tab = Tab(rawValue: t) { model.tab = tab }  // debugging
        if let q = arg("--query", in: CommandLine.arguments) { model.query = q }
        if CommandLine.arguments.contains("--edit-first") { model.editFirstWhenReady() }
        Launcher.tidy()
        model.refresh()
        Timer.scheduledTimer(withTimeInterval: CLI.interval, repeats: true) { [weak self] _ in Task { @MainActor in self?.model.refresh() } }
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.model.refresh() }
        }
        if CommandLine.arguments.contains("--open-on-launch") { showWhenReady(tries: 0) }
    }

    func updateBadge(_ n: Int) {
        guard let b = statusItem.button else { return }
        b.title = n > 0 ? " \(n)" : ""
        b.toolTip = n > 0 ? "chainlog: \(n) unsaved session\(n == 1 ? "" : "s")" : "chainlog"
    }

    @objc func toggle(_ sender: Any?) {
        if popover.isShown { popover.performClose(sender) } else { show() }
    }

    func show() {
        guard let b = statusItem.button else { return }
        NSApp.activate(ignoringOtherApps: true)
        popover.show(relativeTo: b.bounds, of: b, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
        model.refresh()  // what you see is at most a moment old
    }

    func showWhenReady(tries: Int) {
        if model.data == nil && model.problem == nil && tries < 60 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in self?.showWhenReady(tries: tries + 1) }
            return
        }
        show()
    }

    func popoverDidClose(_ notification: Notification) {
        model.clearSearch()      // the next time starts fresh; the tab you were on is remembered
        model.cancelSave()
    }
}

// MARK: - headless switches

func arg(_ name: String, in args: [String]) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

@MainActor
func dumpModel(_ m: AppModel) {
    func line(_ r: Row) -> String {
        let mark = "[\(r.live ? "running" : (r.saved == "user" ? "saved" : (r.saved == "auto" ? "auto-saved" : "not saved")))]"
        return "  \(mark) \(r.starred ? "★ " : "")\(r.canResume ? "" : "(can't reopen) ")\(clip(r.title, 60))\(r.statusLabel.map { " <\($0)>" } ?? "") — \(r.detail)"
    }
    let counts = Tab.allCases.map { t in "\(t.title)\(m.count(for: t).map { " \($0)" } ?? "")\(t == m.tab ? "*" : "")" }
    print("tabs: " + counts.joined(separator: " | "))
    print("search: \"\(m.query)\"")
    guard m.data != nil else {
        let p = ProblemView(problem: m.problem)
        print("problem: \(m.problem ?? "loading") — \(p.title). \(p.detail)")
        return
    }
    if m.tab == .chains {
        let chains = m.chains
        if chains.isEmpty { print("(empty: \(EmptyState(model: m).title))") }
        for c in chains {
            print("\(c.label) · \(c.status ?? "no status") · \(c.rounds) round\(c.rounds == 1 ? "" : "s") · \(c.ago)")
            if let latest = c.members.last, latest.canResume { print("  → Continue the latest round (r\(c.rounds))") }
            for r in c.members.reversed() { print("  r\(r.round ?? 0)" + line(r)) }
        }
        return
    }
    let sections = m.sections
    if sections.allSatisfy({ $0.rows.isEmpty }) { print("(empty: \(EmptyState(model: m).title) — \(EmptyState(model: m).detail))") }
    for s in sections {
        print(s.title)
        for r in s.rows { print(line(r)) }
        if let n = s.reviewAll { print("  → Review all \(n) unsaved…") }
    }
}

/// A model filled from the command line the way the real app fills it.
@MainActor
func headlessModel(_ args: [String]) -> AppModel? {
    let m = AppModel()
    let file = args.firstIndex(of: "--dump").flatMap { i in i + 1 < args.count && !args[i + 1].hasPrefix("--") ? args[i + 1] : nil }
        ?? arg("--data", in: args)
    if let f = file {
        guard let raw = FileManager.default.contents(atPath: f), let d = try? JSONDecoder().decode(MenuData.self, from: raw) else { return nil }
        m.data = d
    } else if let d = CLI.fetch().data {
        m.data = d
    } else { return nil }
    if let t = arg("--tab", in: args), let tab = Tab(rawValue: t) { m.tab = tab }
    if let ff = arg("--find-file", in: args) {
        let rows = FileManager.default.contents(atPath: ff).flatMap { try? JSONDecoder().decode(FindData.self, from: $0) }?.results
        m.finder = { _ in rows }
    }
    if let q = arg("--query", in: args) { m.query = q; m.answerSearchNow() }
    if args.contains("--edit-first"), let r = m.visibleRows.first { m.beginSave(r) }
    return m
}

/// Draw the popover into a PNG, as the system would.
@MainActor
func render(_ m: AppModel, to path: String, dark: Bool) -> Bool {
    let size = NSSize(width: 440, height: 560)
    let root = PopoverView(model: m).background(Color(nsColor: .windowBackgroundColor))
    let host = NSHostingView(rootView: root)
    host.frame = NSRect(origin: .zero, size: size)
    let win = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
    win.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
    win.contentView = host
    host.layoutSubtreeIfNeeded()
    RunLoop.current.run(until: Date().addingTimeInterval(0.6))  // let SwiftUI lay out and the search field settle
    host.layoutSubtreeIfNeeded()
    guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { return false }
    host.cacheDisplay(in: host.bounds, to: rep)
    guard let png = rep.representation(using: .png, properties: [:]) else { return false }
    return (try? png.write(to: URL(fileURLWithPath: path))) != nil
}

@main
struct ChainlogMain {
    @MainActor
    static func main() {
        let args = CommandLine.arguments
        if let name = arg("--dump-problem", in: args) {
            let m = AppModel()
            m.problem = name == "loading" ? nil : name
            dumpModel(m)
            exit(0)
        }
        if args.contains("--dump") || args.contains("--render") {
            guard let m = headlessModel(args) else { FileHandle.standardError.write(Data("could not read menu data\n".utf8)); exit(1) }
            if let out = arg("--render", in: args) {
                _ = NSApplication.shared  // a window server connection
                let ok = render(m, to: out, dark: args.contains("--dark"))
                print(ok ? "wrote \(out)" : "render failed")
                exit(ok ? 0 : 1)
            }
            dumpModel(m)
            exit(0)
        }
        if let i = args.firstIndex(of: "--print-script"), i + 1 < args.count {
            print(Launcher.script(for: args[i + 1]), terminator: "")
            exit(0)
        }
        if let i = args.firstIndex(of: "--launch"), i + 2 < args.count {
            _ = NSApplication.shared  // a window server connection, as when the app runs
            let opened = Launcher.open(args[i + 2], name: args[i + 1])
            RunLoop.current.run(until: Date().addingTimeInterval(3))  // let the asynchronous open finish
            print(opened ? "opened" : "copied")
            exit(opened ? 0 : 3)
        }
        if let i = args.firstIndex(of: "--act"), i + 2 < args.count {
            let what = args[i + 1], ref = args[i + 2]
            let r: (ok: Bool, message: String)
            switch what {
            case "star": r = Actions.star(ref: ref, on: true)
            case "unstar": r = Actions.star(ref: ref, on: false)
            case "save" where i + 4 < args.count:
                r = Actions.save(ref: ref, note: args[i + 4], status: args[i + 3] == "-" ? nil : args[i + 3])
            default: print("failed: unknown action"); exit(2)
            }
            print(r.ok ? "ok" : "failed: \(r.message)")
            exit(r.ok ? 0 : 1)
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
