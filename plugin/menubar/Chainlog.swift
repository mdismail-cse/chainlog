// Chainlog: a Mac menu bar app for chainlog. Click the icon to see your unsaved and recent
// Claude Code / Codex sessions and PR chains; click a session to reopen it in a terminal.
//
// It holds no data of its own. It runs `chainlog _menu` (JSON, schema chainlog.menu/1), draws the
// lists, and runs the exact resume command chainlog gives it. Build: `sh build.sh`.
//
//   Chainlog --dump [sample.json]    print the menu as text and exit (no window; used by tests)
//   Chainlog --open-on-launch        open the menu once at start (for screenshots)
import AppKit
import ServiceManagement

// MARK: - data (chainlog.menu/1)

struct ResumeInfo: Decodable {
    let cwd: String
    let command: String
    let blockers: [String]
    let notes: [String]
}

struct Row: Decodable {
    let ref: String
    let short: String
    let harness: String
    let title: String
    let saved: String          // user | auto | none
    let status: String?
    let live: Bool
    let chain: String?
    let round: Int?
    let ago: String
    let last_work: String?
    let resume: ResumeInfo
}

struct ChainInfo: Decodable {
    let id: String
    let rounds: Int
    let status: String?
    let title: String
    let ago: String
    let members: [Row]
}

struct Counts: Decodable { let unsaved: Int }

struct MenuData: Decodable {
    let schema: String
    let chainlog: String
    let counts: Counts
    let unsaved: [Row]
    let recent: [Row]
    let chains: [ChainInfo]
}

/// `CHAINLOG_MENUBAR_DEBUG=1` prints what the app is doing to stderr.
func dbg(_ text: @autoclosure () -> String) {
    if ProcessInfo.processInfo.environment["CHAINLOG_MENUBAR_DEBUG"] != nil {
        FileHandle.standardError.write(Data(("chainlog-menubar: " + text() + "\n").utf8))
    }
}

// MARK: - running chainlog

enum CLI {
    static var overridePath: String? { ProcessInfo.processInfo.environment["CHAINLOG_BIN"] }

    /// The `chainlog` launcher. A menu bar app does not get the Terminal's PATH, so look in the usual places.
    static func locate() -> String? {
        let fm = FileManager.default
        let home = NSHomeDirectory()
        var candidates = ["\(home)/.local/bin/chainlog", "/opt/homebrew/bin/chainlog", "/usr/local/bin/chainlog"]
        if let o = overridePath { candidates.insert(o, at: 0) }
        for c in candidates where fm.isExecutableFile(atPath: c) { return c }
        // last resort: ask a login shell, which has the PATH from ~/.zprofile
        let r = run("/bin/zsh", ["-lc", "command -v chainlog"], timeout: 5)
        if let out = r.out, let path = String(data: out, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
           !path.isEmpty, fm.isExecutableFile(atPath: path) { return path }
        return nil
    }

    static func run(_ exe: String, _ args: [String], timeout: TimeInterval = 20) -> (out: Data?, status: Int32) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["CHAINLOG_NO_PROMPT"] = "1"
        let home = NSHomeDirectory()
        env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return (nil, -1) }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { if p.isRunning { p.terminate() } }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (data, p.terminationStatus)
    }

    /// nil when chainlog is missing or its answer cannot be read.
    static func fetch() -> (data: MenuData?, problem: String?, raw: Data?) {
        guard let exe = locate() else { dbg("chainlog not found"); return (nil, "notfound", nil) }
        let r = run(exe, ["_menu"])
        dbg("ran \(exe) _menu -> status \(r.status), \(r.out?.count ?? 0) bytes")
        guard r.status == 0, let out = r.out, !out.isEmpty else { return (nil, "failed", nil) }
        do {
            let d = try JSONDecoder().decode(MenuData.self, from: out)
            return d.schema.hasPrefix("chainlog.menu/1") ? (d, nil, out) : (nil, "newer", nil)
        } catch { return (nil, "failed", nil) }
    }
}

func shellQuote(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

// MARK: - opening a session in a terminal

enum TerminalChoice: String, CaseIterable {
    case terminal, iterm, copy

    var label: String {
        switch self {
        case .terminal: return "Terminal"
        case .iterm: return "iTerm2"
        case .copy: return "Only copy the command"
        }
    }
    var bundleID: String? {
        switch self {
        case .terminal: return "com.apple.Terminal"
        case .iterm: return "com.googlecode.iterm2"
        case .copy: return nil
        }
    }
    var available: Bool {
        guard let id = bundleID else { return true }
        return NSWorkspace.shared.urlForApplication(withBundleIdentifier: id) != nil
    }
    static var current: TerminalChoice {
        get {
            let c = UserDefaults.standard.string(forKey: "terminal").flatMap(TerminalChoice.init(rawValue:)) ?? .terminal
            return c.available ? c : .terminal
        }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: "terminal") }
    }
}

enum Launcher {
    /// Scripts live next to chainlog's data, private to the user.
    static var runDir: URL {
        let env = ProcessInfo.processInfo.environment
        let base = env["CHAINLOG_HOME"] ?? (env["XDG_DATA_HOME"].map { $0 + "/chainlog" } ?? NSHomeDirectory() + "/.local/share/chainlog")
        return URL(fileURLWithPath: base).appendingPathComponent("run")
    }

    static func userShell() -> String {
        if let pw = getpwuid(getuid()), let s = pw.pointee.pw_shell {
            let path = String(cString: s)
            if FileManager.default.isExecutableFile(atPath: path) { return path }
        }
        return "/bin/zsh"
    }

    static func tidy() {
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(at: runDir, includingPropertiesForKeys: [.contentModificationDateKey]) else { return }
        for f in files where f.pathExtension == "command" {
            let m = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? Date()
            if Date().timeIntervalSince(m) > 86_400 { try? fm.removeItem(at: f) }
        }
    }

    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    /// Run `command` in a new terminal window. Returns false when it could only be copied.
    @discardableResult
    static func open(_ command: String, name: String) -> Bool {
        let choice = TerminalChoice.current
        guard let id = choice.bundleID, let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: id) else {
            copy(command)
            return false
        }
        // An interactive login shell, so the command finds `claude` / `codex` on the same PATH as your Terminal.
        let script = "#!\(userShell()) -il\n\(command)\nexec \"$SHELL\" -il\n"
        let safe = name.filter { $0.isLetter || $0.isNumber || $0 == "-" }
        let file = runDir.appendingPathComponent("\(safe.isEmpty ? "session" : safe).command")
        do {
            try FileManager.default.createDirectory(at: runDir, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            try script.write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        } catch {
            copy(command)
            return false
        }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = true
        NSWorkspace.shared.open([file], withApplicationAt: app, configuration: cfg) { _, _ in }
        return true
    }
}

// MARK: - building the menu

func clip(_ s: String, _ n: Int) -> String { s.count <= n ? s : String(s.prefix(n - 1)) + "…" }

enum MenuBuilder {
    static let rightEdge: CGFloat = 400

    /// The same four states as the terminal list: ● saved, ◐ auto-saved, ○ not saved, ▶ running.
    static func mark(_ r: Row) -> (symbol: String, label: String) {
        if r.live { return ("play.fill", "running") }
        switch r.saved {
        case "user": return ("circle.fill", "saved")
        case "auto": return ("circle.lefthalf.filled", "auto-saved")
        default: return ("circle", "not saved")
        }
    }

    static func icon(_ r: Row) -> NSImage? {
        let m = mark(r)
        let img = NSImage(systemSymbolName: m.symbol, accessibilityDescription: m.label)
        img?.isTemplate = true
        return img
    }

    static func line(_ left: String, _ right: String, enabled: Bool = true) -> NSAttributedString {
        let ps = NSMutableParagraphStyle()
        ps.tabStops = [NSTextTab(textAlignment: .right, location: rightEdge, options: [:])]
        ps.lineBreakMode = .byClipping
        let out = NSMutableAttributedString(string: left, attributes: [
            .font: NSFont.menuFont(ofSize: 0), .paragraphStyle: ps,
            .foregroundColor: enabled ? NSColor.labelColor : NSColor.disabledControlTextColor])
        if !right.isEmpty {
            out.append(NSAttributedString(string: "\t" + right, attributes: [
                .font: NSFont.menuFont(ofSize: 11), .paragraphStyle: ps, .foregroundColor: NSColor.secondaryLabelColor]))
        }
        return out
    }

    static func header(_ text: String) -> NSMenuItem {
        if #available(macOS 14.0, *) { return NSMenuItem.sectionHeader(title: text) }
        let item = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        item.attributedTitle = NSAttributedString(string: text, attributes: [
            .font: NSFont.systemFont(ofSize: 11, weight: .semibold), .foregroundColor: NSColor.secondaryLabelColor])
        item.isEnabled = false
        return item
    }

    static func note(_ text: String) -> NSMenuItem {
        let item = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        item.attributedTitle = NSAttributedString(string: text, attributes: [
            .font: NSFont.menuFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor])
        item.isEnabled = false
        return item
    }

    static func rowItem(_ r: Row, target: AnyObject?, action: Selector?) -> NSMenuItem {
        var right: [String] = []
        if let c = r.chain { right.append(c + (r.round.map { " r\($0)" } ?? "")) }
        if let s = r.status { right.append(s) }
        right.append(r.ago)
        let blocked = !r.resume.blockers.isEmpty
        let item = NSMenuItem(title: r.title, action: blocked ? nil : action, keyEquivalent: "")
        item.attributedTitle = line(clip(r.title, 44), right.joined(separator: "  "), enabled: !blocked)
        item.image = icon(r)
        item.representedObject = r
        item.target = target
        item.isEnabled = !blocked
        var tip = [r.title]
        if let w = r.last_work { tip.append("Last: " + clip(w, 160)) }
        tip.append(contentsOf: r.resume.blockers.map { "Can't reopen: " + $0 })
        item.toolTip = tip.joined(separator: "\n")
        return item
    }

    static func chainItem(_ c: ChainInfo, target: AnyObject?, action: Selector?) -> NSMenuItem {
        let item = NSMenuItem(title: c.id, action: nil, keyEquivalent: "")
        let tail = [c.status, "\(c.rounds) round\(c.rounds == 1 ? "" : "s")", c.ago].compactMap { $0 }
        item.attributedTitle = line(c.id, tail.joined(separator: "  "))
        let sub = NSMenu(title: c.id)
        sub.autoenablesItems = false
        if let latest = c.members.last, latest.resume.blockers.isEmpty {
            let go = rowItem(latest, target: target, action: action)
            go.attributedTitle = line("Continue the latest round (r\(c.rounds))", latest.ago)
            sub.addItem(go)
            sub.addItem(.separator())
        }
        for m in c.members.reversed() {
            let it = rowItem(m, target: target, action: action)
            it.attributedTitle = line("r\(m.round ?? 0)  \(clip(m.title, 38))",
                                      [m.status, m.harness, m.ago].compactMap { $0 }.joined(separator: "  "),
                                      enabled: m.resume.blockers.isEmpty)
            sub.addItem(it)
        }
        item.submenu = sub
        return item
    }

    /// Everything but the footer (preferences, quit), which needs the app's own actions.
    static func lists(_ d: MenuData, target: AnyObject?, open: Selector?, review: Selector?) -> [NSMenuItem] {
        var items: [NSMenuItem] = []
        if d.unsaved.isEmpty {
            items.append(header("Unsaved"))
            items.append(note("Nothing unsaved. Everything you left has a note."))
        } else {
            items.append(header("Unsaved · \(d.counts.unsaved)"))
            items.append(contentsOf: d.unsaved.map { rowItem($0, target: target, action: open) })
            if d.counts.unsaved > d.unsaved.count {
                let more = NSMenuItem(title: "Review all \(d.counts.unsaved) unsaved…", action: review, keyEquivalent: "")
                more.target = target
                items.append(more)
            }
        }
        if !d.recent.isEmpty {
            items.append(.separator())
            items.append(header("Recent"))
            items.append(contentsOf: d.recent.map { rowItem($0, target: target, action: open) })
        }
        if !d.chains.isEmpty {
            items.append(.separator())
            items.append(header("PR chains"))
            items.append(contentsOf: d.chains.map { chainItem($0, target: target, action: open) })
        }
        return items
    }
}

func dumpMenu(_ menu: NSMenu, _ indent: String = "") {
    for item in menu.items {
        if item.isSeparatorItem { print("\(indent)────"); continue }
        let text = (item.attributedTitle?.string ?? item.title).replacingOccurrences(of: "\t", with: "   ")
        let mark = item.image?.accessibilityDescription.map { "[\($0)] " } ?? ""
        print("\(indent)\(item.isEnabled ? "" : "(off) ")\(mark)\(text)\(item.state == .on ? "  ✓" : "")")
        if let sub = item.submenu { dumpMenu(sub, indent + "    ") }
    }
}

// MARK: - the app

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let menu = NSMenu()
    var data: MenuData?
    var problem: String?
    var flashTimer: Timer?
    var menuIsOpen = false
    var lastRaw: Data?

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.setActivationPolicy(.accessory)
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
        if let b = statusItem.button {
            b.image = NSImage(systemSymbolName: "link", accessibilityDescription: "chainlog")
            b.imagePosition = .imageLeading
        }
        Launcher.tidy()
        refresh()
        Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in Task { @MainActor in self?.refresh() } }
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        if CommandLine.arguments.contains("--open-on-launch") { popUpWhenReady(tries: 0) }
    }

    /// debugging / screenshots: show the same menu at the top left of the main screen once there is data
    func popUpWhenReady(tries: Int) {
        if data == nil && problem == nil && tries < 60 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in self?.popUpWhenReady(tries: tries + 1) }
            return
        }
        guard let screen = NSScreen.main else { return }
        NSApp.activate(ignoringOtherApps: true)
        menu.popUp(positioning: nil, at: NSPoint(x: screen.frame.minX + 60, y: screen.visibleFrame.maxY - 6), in: nil)
    }

    /// Ask chainlog again off the main thread; the badge and the next menu use the answer.
    func refresh() {
        dbg("refresh")
        DispatchQueue.global(qos: .utility).async {
            let r = CLI.fetch()
            dbg("fetched: data=\(r.data != nil) problem=\(r.problem ?? "none")")
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    let changed = r.raw != self.lastRaw || r.problem != self.problem
                    self.data = r.data
                    self.problem = r.problem
                    self.lastRaw = r.raw
                    self.updateBadge()
                    if self.menuIsOpen && changed { self.rebuild(self.menu) }  // an answer that arrives while the menu is open
                }
            }
        }
    }

    func updateBadge() {
        guard let b = statusItem.button else { return }
        let n = data?.counts.unsaved ?? 0
        b.title = n > 0 ? " \(n)" : ""
        b.toolTip = n > 0 ? "chainlog: \(n) unsaved session\(n == 1 ? "" : "s")" : "chainlog"
    }

    func flash(_ text: String) {
        guard let b = statusItem.button else { return }
        b.title = " " + text
        flashTimer?.invalidate()
        flashTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: false) { [weak self] _ in
            Task { @MainActor in self?.updateBadge() }
        }
    }

    // MARK: menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        rebuild(menu)
        refresh()  // the next click shows what is true now
    }

    func menuWillOpen(_ menu: NSMenu) { menuIsOpen = true }
    func menuDidClose(_ menu: NSMenu) { menuIsOpen = false }

    func rebuild(_ menu: NSMenu) {
        menu.removeAllItems()
        if let d = data {
            for it in MenuBuilder.lists(d, target: self, open: #selector(openSession(_:)), review: #selector(reviewAll)) {
                menu.addItem(it)
            }
            menu.addItem(.separator())
            menu.addItem(MenuBuilder.note("Click a session to reopen it · ⌥-click copies its command"))
        } else {
            menu.addItem(MenuBuilder.header("chainlog"))
            switch problem {
            case "notfound":
                menu.addItem(MenuBuilder.note("The chainlog command was not found."))
                let how = NSMenuItem(title: "How to install chainlog…", action: #selector(openHelp), keyEquivalent: "")
                how.target = self
                menu.addItem(how)
            case "newer":
                menu.addItem(MenuBuilder.note("chainlog is newer than this app. Run: chainlog setup menubar"))
            case nil:
                menu.addItem(MenuBuilder.note("Loading…"))
            default:
                menu.addItem(MenuBuilder.note("Could not read chainlog's answer. Update chainlog, or try: chainlog doctor"))
            }
        }
        menu.addItem(.separator())
        let term = NSMenuItem(title: "Open chainlog in \(TerminalChoice.current == .copy ? "a terminal" : TerminalChoice.current.label)",
                              action: #selector(openChainlog), keyEquivalent: "")
        term.target = self
        menu.addItem(term)
        let prefs = NSMenuItem(title: "Open sessions in", action: nil, keyEquivalent: "")
        let sub = NSMenu(title: "Open sessions in")
        sub.autoenablesItems = false
        for c in TerminalChoice.allCases {
            let it = NSMenuItem(title: c.label + (c.available ? "" : " (not installed)"), action: c.available ? #selector(pickTerminal(_:)) : nil, keyEquivalent: "")
            it.target = self
            it.representedObject = c.rawValue
            it.state = TerminalChoice.current == c ? .on : .off
            it.isEnabled = c.available
            sub.addItem(it)
        }
        prefs.submenu = sub
        menu.addItem(prefs)
        let login = NSMenuItem(title: "Open at login", action: #selector(toggleLogin), keyEquivalent: "")
        login.target = self
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        let r = NSMenuItem(title: "Refresh now", action: #selector(refreshNow), keyEquivalent: "r")
        r.target = self
        menu.addItem(r)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Chainlog", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
    }

    // MARK: actions

    @objc func openSession(_ sender: NSMenuItem) {
        guard let r = sender.representedObject as? Row else { return }
        let copyOnly = NSEvent.modifierFlags.contains(.option)
        if copyOnly {
            Launcher.copy(r.resume.command)
            flash("copied")
        } else if !Launcher.open(r.resume.command, name: "resume-" + r.short) {
            flash("copied")  // no terminal to open: the command is on the clipboard
        }
    }

    @objc func reviewAll() { runChainlog(["review"], name: "review") }
    @objc func openChainlog() { runChainlog([], name: "chainlog") }
    @objc func refreshNow() { refresh() }
    @objc func openHelp() { NSWorkspace.shared.open(URL(string: "https://github.com/mdismail-cse/chainlog#step-1-get-chainlog-once")!) }

    func runChainlog(_ args: [String], name: String) {
        guard let exe = CLI.locate() else { problem = "notfound"; return }
        let cmd = ([shellQuote(exe)] + args.map(shellQuote)).joined(separator: " ")
        if !Launcher.open(cmd, name: name) { flash("copied") }
    }

    @objc func pickTerminal(_ sender: NSMenuItem) {
        if let raw = sender.representedObject as? String, let c = TerminalChoice(rawValue: raw) { TerminalChoice.current = c }
    }

    @objc func toggleLogin() {
        let svc = SMAppService.mainApp
        do {
            if svc.status == .enabled { try svc.unregister() } else { try svc.register() }
        } catch {
            let a = NSAlert()
            a.messageText = "Could not change \"Open at login\""
            a.informativeText = "Move Chainlog.app to ~/Applications or /Applications and try again.\n\n\(error.localizedDescription)"
            a.runModal()
        }
    }
}

@main
struct ChainlogMain {
    @MainActor
    static func main() {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--dump") {
            // headless: build the menu from a sample file (or from chainlog itself) and print it
            var d: MenuData?
            if i + 1 < args.count, let raw = FileManager.default.contents(atPath: args[i + 1]) {
                d = try? JSONDecoder().decode(MenuData.self, from: raw)
            } else {
                d = CLI.fetch().data
            }
            guard let data = d else { FileHandle.standardError.write(Data("could not read menu data\n".utf8)); exit(1) }
            let m = NSMenu()
            m.autoenablesItems = false
            for it in MenuBuilder.lists(data, target: nil, open: nil, review: nil) { m.addItem(it) }
            dumpMenu(m)
            exit(0)
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
