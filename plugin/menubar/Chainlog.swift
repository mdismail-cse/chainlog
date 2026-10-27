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
//   --hover-first                       (with --render, --dump or --open-on-launch) show the preview panel for the first row
//   --edit-first                        (with --render or --open-on-launch) open the "save with a note" editor on the first row
//   --selftest SAMPLE.json              run the model's own checks (drafts, tabs, search, confirmations …); exit 1 on a failure
//   --unregister                        take the app out of Login Items (run before deleting it)
//   --simulate-reclick                  replay two clicks on the icon and log whether the second closes it
//   --open-on-launch                    show the popover once the first answer is in, and keep it open
import AppKit
import Combine
import ServiceManagement
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let popover = NSPopover()
    let model = AppModel()
    var cancellables = Set<AnyCancellable>()
    var toggleGuard = ToggleGuard()
    var anchorWindow: NSWindow?  // an invisible 2-pt window the popover hangs from, on the display that was clicked
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
        if CommandLine.arguments.contains("--hover-first") { model.hoverFirstWhenReady() }
        Launcher.tidy()
        model.refresh()
        // both run on the main thread, and the app lives as long as the process: a plain capture is enough
        // (a weak `self` used inside a Task is a compile error on Swift 5.9/5.10, the toolchain on macos-14)
        Timer.scheduledTimer(withTimeInterval: CLI.interval, repeats: true) { _ in MainActor.assumeIsolated { self.model.refresh() } }
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { self.model.refresh() }
        }
        if CommandLine.arguments.contains("--open-on-launch") { showWhenReady(tries: 0) }
        if CommandLine.arguments.contains("--simulate-reclick") { simulateReclick() }
    }

    /// Debugging: do what two real clicks on the icon do, and log the outcome ("reclick: closed" is the fixed behaviour).
    /// Click 1 opens. Click 2: the transient popover closes on mouse down, then the icon's action runs a moment later.
    func simulateReclick() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            self.toggle(nil)                                   // click 1
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                dbg("reclick: open after click 1 = \(self.popover.isShown)")
                self.popover.performClose(nil)                 // click 2, mouse down: the popover closes itself
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                    self.toggle(nil)                           // click 2, mouse up: the icon's action
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                        dbg("reclick: \(self.popover.isShown ? "STILL OPEN (bug)" : "closed")")
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
                            self.toggle(nil)                   // a later, deliberate click opens it again
                            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                                dbg("reclick: later click reopens = \(self.popover.isShown)")
                                exit(0)
                            }
                        }
                    }
                }
            }
        }
    }

    func updateBadge(_ n: Int) {
        guard let b = statusItem.button else { return }
        b.title = n > 0 ? " \(n)" : ""
        b.toolTip = n > 0 ? "chainlog: \(n) unsaved session\(n == 1 ? "" : "s")" : "chainlog"
    }

    @objc func toggle(_ sender: Any?) {
        if popover.isShown { popover.performClose(sender); return }
        guard toggleGuard.shouldOpen(at: Date()) else { dbg("that click is what just closed the popover: not reopening it"); return }
        show()
    }

    /// Where the user clicked (tests can say where with CHAINLOG_MENUBAR_ANCHOR=x,y, in screen coordinates).
    func clickPoint() -> NSPoint {
        if let o = ProcessInfo.processInfo.environment["CHAINLOG_MENUBAR_ANCHOR"] {
            let p = o.split(separator: ",").compactMap { Double($0) }
            if p.count == 2 { return NSPoint(x: p[0], y: p[1]) }
        }
        return NSEvent.mouseLocation
    }

    /// An invisible window at `point` for the popover to hang from. Anchoring to the status item itself is what put the
    /// popover in the wrong place on a second display.
    func anchorView(at point: NSPoint) -> NSView {
        let w = anchorWindow ?? {
            let w = NSWindow(contentRect: .zero, styleMask: .borderless, backing: .buffered, defer: false)
            w.isOpaque = false
            w.backgroundColor = .clear
            w.hasShadow = false
            w.ignoresMouseEvents = true
            w.level = .statusBar
            w.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
            w.isReleasedWhenClosed = false
            w.contentView = NSView()
            return w
        }()
        w.setFrame(NSRect(x: point.x - 1, y: point.y - 1, width: 2, height: 1), display: false)
        w.orderFrontRegardless()
        anchorWindow = w
        return w.contentView!
    }

    func show() {
        let screens = NSScreen.screens
        guard !screens.isEmpty else { return }
        let click = clickPoint()
        let screen = screens[Placement.screenIndex(for: click, frames: screens.map { $0.frame })]
        let point = Placement.anchor(click: click, visible: screen.visibleFrame, frame: screen.frame)
        NSApp.activate(ignoringOtherApps: true)
        let anchor = anchorView(at: point)
        dbg("click=\(click) -> display \(screen.frame), opening under \(point)")
        popover.show(relativeTo: anchor.bounds, of: anchor, preferredEdge: .minY)
        dbg("popover window frame=\(popover.contentViewController?.view.window?.frame ?? .zero)")
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
        toggleGuard.closed(at: Date())
        anchorWindow?.orderOut(nil)
        model.popoverClosed()    // the next time starts with an empty search; the tab and any half-written note are kept
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
        return "  \(mark) \(r.starred ? "★ " : "")\(r.canResume ? "" : "(can't reopen) ")\(r.canResume && r.warning != nil ? "(asks first) " : "")\(clip(r.title, 60))\(r.statusLabel.map { " <\($0)>" } ?? "") — \(r.detail)"
    }
    let counts = Tab.allCases.map { t in "\(t.title)\(m.count(for: t).map { " \($0)" } ?? "")\(t == m.tab ? "*" : "")" }
    print("tabs: " + counts.joined(separator: " | "))
    print("search: \"\(m.query)\"")
    if let r = m.hover.row { print("preview: \(clip(r.title, 60)) — \(r.last_work.map { clip($0, 80) } ?? "nothing said yet") — \(r.folder)") }
    defer { if m.data != nil { print("enter opens: \(m.visibleRows.first(where: { $0.canResume }).map { clip($0.title, 60) + ($0.round.map { " (r\($0))" } ?? "") } ?? "nothing")") } }
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
        if let note = s.note { print("  (\(note))") }
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
        m.finder = { _, _ in rows }
    }
    if let q = arg("--query", in: args) { m.query = q; m.answerSearchNow() }
    if args.contains("--edit-first"), let r = m.visibleRows.first { m.beginSave(r) }
    if args.contains("--hover-first"), let r = m.visibleRows.first { m.hover.enter(r) }
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
        if args.contains("--unregister") {  // before the app is deleted: take it out of System Settings > Login Items
            let svc = SMAppService.mainApp
            if svc.status == .enabled || svc.status == .requiresApproval { try? svc.unregister() }
            let state: String
            switch svc.status {
            case .enabled: state = "still enabled"
            case .requiresApproval: state = "still waiting for approval"
            case .notRegistered: state = "not registered"
            default: state = "not found"
            }
            print("login item: \(state)")
            exit(svc.status == .enabled || svc.status == .requiresApproval ? 1 : 0)
        }
        if args.contains("--selftest") {
            let failures = selfTest(sample: arg("--selftest", in: args))
            exit(failures == 0 ? 0 : 1)
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
