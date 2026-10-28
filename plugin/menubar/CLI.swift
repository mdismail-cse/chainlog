// Everything that touches the outside world: running `chainlog`, and opening a terminal.
import AppKit
import ServiceManagement

/// `CHAINLOG_MENUBAR_DEBUG=1` prints what the app is doing to stderr.
func dbg(_ text: @autoclosure () -> String) {
    if ProcessInfo.processInfo.environment["CHAINLOG_MENUBAR_DEBUG"] != nil {
        FileHandle.standardError.write(Data(("chainlog-menubar: " + text() + "\n").utf8))
    }
}

func shellQuote(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

// MARK: - running chainlog

enum CLI {
    static var overridePath: String? { ProcessInfo.processInfo.environment["CHAINLOG_BIN"] }
    static var lastShellLookup: Date?

    /// The `chainlog` launcher. A menu bar app does not get the Terminal's PATH, so look in the usual places.
    static func locate() -> String? {
        let fm = FileManager.default
        let home = homeDirectory()
        var candidates = ["\(home)/.local/bin/chainlog", "/opt/homebrew/bin/chainlog", "/usr/local/bin/chainlog"]
        if let o = overridePath { candidates.insert(o, at: 0) }
        for c in candidates where fm.isExecutableFile(atPath: c) { return c }
        // last resort: ask a login shell, which has the PATH from ~/.zprofile (at most once every 5 minutes)
        if let last = lastShellLookup, Date().timeIntervalSince(last) < 300 { return nil }
        lastShellLookup = Date()
        let r = run("/bin/zsh", ["-lc", "command -v chainlog"], timeout: 5)
        if let out = r.out, let path = String(data: out, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
           !path.isEmpty, fm.isExecutableFile(atPath: path) { return path }
        return nil
    }

    /// Seconds between automatic refreshes (CHAINLOG_MENUBAR_INTERVAL overrides, for tests).
    static var interval: TimeInterval {
        ProcessInfo.processInfo.environment["CHAINLOG_MENUBAR_INTERVAL"].flatMap(TimeInterval.init) ?? 60
    }

    /// Seconds before a chainlog call is given up on (CHAINLOG_MENUBAR_TIMEOUT overrides, for tests).
    static var timeout: TimeInterval {
        ProcessInfo.processInfo.environment["CHAINLOG_MENUBAR_TIMEOUT"].flatMap(TimeInterval.init) ?? 20
    }

    /// Runs a program and returns what it printed. Never blocks past `timeout`, even when the program
    /// leaves a child behind that keeps its output open: the read is event-driven, not "until EOF".
    static func run(_ exe: String, _ args: [String], timeout: TimeInterval = CLI.timeout,
                    stdin input: String? = nil, mergeStderr: Bool = false) -> (out: Data?, status: Int32) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        env["CHAINLOG_NO_PROMPT"] = "1"
        let home = homeDirectory()
        env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        p.environment = env
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = mergeStderr ? pipe : FileHandle.nullDevice
        let inPipe = Pipe()
        p.standardInput = input == nil ? FileHandle.nullDevice : inPipe

        let lock = NSLock()
        var buffer = Data()
        let exited = DispatchSemaphore(value: 0)
        let eof = DispatchSemaphore(value: 0)
        pipe.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; eof.signal(); return }
            lock.lock(); buffer.append(d); lock.unlock()
        }
        p.terminationHandler = { _ in exited.signal() }
        do { try p.run() } catch { pipe.fileHandleForReading.readabilityHandler = nil; return (nil, -1) }
        if let input = input {
            inPipe.fileHandleForWriting.write(Data(input.utf8))
            try? inPipe.fileHandleForWriting.close()
        }

        if exited.wait(timeout: .now() + timeout) == .timedOut {
            dbg("timed out after \(Int(timeout))s: \(exe)")
            p.terminate()
            if exited.wait(timeout: .now() + 2) == .timedOut { kill(p.processIdentifier, SIGKILL) }
            pipe.fileHandleForReading.readabilityHandler = nil
            return (nil, -2)
        }
        _ = eof.wait(timeout: .now() + 1)  // normally immediate; a child that kept the pipe open costs one second
        pipe.fileHandleForReading.readabilityHandler = nil
        lock.lock(); defer { lock.unlock() }
        return (buffer, p.terminationStatus)
    }

    /// nil data when chainlog is missing or its answer cannot be read.
    static func fetch() -> (data: MenuData?, problem: String?, raw: Data?) {
        guard let exe = locate() else { dbg("chainlog not found"); return (nil, "notfound", nil) }
        let r = run(exe, ["_menu"])
        dbg("ran \(exe) _menu -> status \(r.status), \(r.out?.count ?? 0) bytes")
        guard r.status == 0, let out = r.out, !out.isEmpty else { return (nil, "failed", nil) }
        do {
            let d = try JSONDecoder().decode(MenuData.self, from: out)
            return d.schema.hasPrefix("chainlog.menu/1") ? (d, nil, out) : (nil, "newer", nil)
        } catch { dbg("decode failed: \(error)"); return (nil, "failed", nil) }
    }

    static func findArgument(_ text: String) -> String { "--find=" + text }

    /// Search every session (or only the starred ones). nil when chainlog could not answer.
    /// `--find=TEXT` as one argument: a search that starts with a dash ("--json", "-v") is still text.
    static func find(_ text: String, starredOnly: Bool = false) -> [Row]? {
        guard let exe = locate() else { return nil }
        let r = run(exe, ["_menu", findArgument(text)] + (starredOnly ? ["--starred"] : []))
        dbg("ran _menu --find %@ -> status \(r.status)".replacingOccurrences(of: "%@", with: clip(text, 20)))
        guard r.status == 0, let out = r.out, let d = try? JSONDecoder().decode(FindData.self, from: out) else { return nil }
        return d.results
    }
}

/// What the buttons do. Each is one `chainlog` command, so the terminal and the app can never disagree.
enum Actions {
    static func run(_ args: [String], stdin: String? = nil) -> (ok: Bool, message: String) {
        guard let exe = CLI.locate() else { return (false, "chainlog was not found") }
        let r = CLI.run(exe, args, stdin: stdin, mergeStderr: true)
        let text = r.out.flatMap { String(data: $0, encoding: .utf8) }?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        dbg("ran chainlog \(args.prefix(3).joined(separator: " ")) -> \(r.status) \(clip(text, 80))")
        return (r.status == 0, text)
    }

    static func star(ref: String, on: Bool) -> (ok: Bool, message: String) {
        run([on ? "star" : "unstar", ref])
    }

    /// `chainlog keep REF` / `chainlog unkeep REF --yes` (the app asks first when the copy is all that is left).
    static func keepArguments(ref: String, on: Bool) -> [String] { on ? ["keep", ref] : ["unkeep", ref, "--yes"] }

    /// `chainlog delete REF --from both|claude|chainlog --yes` (the app has asked already).
    static func deleteArguments(ref: String, scope: String) -> [String] { ["delete", ref, "--from", scope, "--yes"] }

    /// `chainlog save --id REF [-s STATUS] -- NOTE`: the `--` keeps a note that starts with a dash a note.
    static func save(ref: String, note: String, status: String?) -> (ok: Bool, message: String) {
        // an empty save would only mark the session as saved without saying anything: refuse it here, whatever chainlog version is installed
        if note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && (status ?? "").isEmpty {
            return (false, "nothing to save: write a note or pick a result")
        }
        var args = ["save", "--id", ref]
        if let s = status, !s.isEmpty { args += ["-s", s] }
        return run(args + ["--", note])
    }
}

/// Ask before doing something chainlog itself would ask about.
enum Alerts {
    /// A one-line question with a text box. nil when cancelled.
    @MainActor
    static func ask(_ title: String, _ detail: String, initial: String) -> String? {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title
        a.informativeText = detail
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 160, height: 24))
        field.stringValue = initial
        a.accessoryView = field
        a.addButton(withTitle: "Set")
        a.addButton(withTitle: "Cancel")
        a.window.initialFirstResponder = field
        return a.runModal() == .alertFirstButtonReturn ? field.stringValue : nil
    }

    @MainActor
    static func confirm(_ title: String, _ detail: String, ok: String) -> Bool {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title
        a.informativeText = detail
        a.addButton(withTitle: "Cancel")  // the default: nothing happens unless you say so
        a.addButton(withTitle: ok)
        return a.runModal() == .alertSecondButtonReturn
    }
}

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
        let base = env["CHAINLOG_HOME"] ?? (env["XDG_DATA_HOME"].map { $0 + "/chainlog" } ?? homeDirectory() + "/.local/share/chainlog")
        return URL(fileURLWithPath: base).appendingPathComponent("run")
    }

    static func userShell() -> String {
        if let pw = getpwuid(getuid()), let s = pw.pointee.pw_shell {
            let path = String(cString: s)
            let known = ["zsh", "bash", "sh", "dash", "ksh", "fish"]  // `-il` and `cd 'x' && cmd` work in these
            if known.contains((path as NSString).lastPathComponent) && FileManager.default.isExecutableFile(atPath: path) { return path }
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

    /// An interactive login shell, so the command finds `claude` / `codex` on the same PATH as your Terminal,
    /// and a shell in the same folder once the session ends.
    static func script(for command: String) -> String {
        "#!\(userShell()) -il\n\(command)\nexec \"$SHELL\" -il\n"
    }

    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    /// Run a shell command in the background (no terminal window). `onFailure` is called on the main queue when it cannot
    /// start or exits with an error. `open` is used by the commands it runs, so no PATH is needed.
    static func shell(_ command: String, onFailure: @escaping @Sendable () -> Void) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", command]
        p.standardInput = FileHandle.nullDevice
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { proc in
            dbg("shell exited \(proc.terminationStatus)")
            if proc.terminationStatus != 0 { DispatchQueue.main.async { onFailure() } }
        }
        do { try p.run() } catch { DispatchQueue.main.async { onFailure() } }
    }

    /// Run `command` in a new terminal window. Returns false when it could only be copied.
    @discardableResult
    static func open(_ command: String, name: String, onFailure: @escaping @Sendable () -> Void = {}) -> Bool {
        let choice = TerminalChoice.current
        guard let id = choice.bundleID, let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: id) else {
            copy(command)
            return false
        }
        let script = script(for: command)
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
        NSWorkspace.shared.open([file], withApplicationAt: app, configuration: cfg) { _, error in
            dbg("open \(file.lastPathComponent) with \(app.lastPathComponent): \(error.map { "\($0)" } ?? "ok")")
            if error != nil { DispatchQueue.main.async { copy(command); onFailure() } }
        }
        return true
    }
}
