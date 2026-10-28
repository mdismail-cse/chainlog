// Where the popover opens. On macOS 26 the system hosts a menu bar item's window itself and gives it no screen at all
// (on a second display its frame even sits above the display's top edge), so anchoring the popover to the item puts it in
// the wrong place. We anchor under the click instead: the display the click was on, the click's x, just under the menu bar.
import AppKit

/// A popover that closes when you click elsewhere closes on the MOUSE DOWN on the menu bar icon; the icon's own click
/// handler then runs, sees it closed, and would open it again, so a second click never closed it. The click that has just
/// closed the popover must therefore not reopen it.
struct ToggleGuard {
    static let window: TimeInterval = 0.6  // longer than a slow click (mouse down to mouse up), shorter than a deliberate reopen
    private(set) var closedAt: Date?
    mutating func closed(at time: Date) { closedAt = time }
    func shouldOpen(at now: Date) -> Bool {
        guard let c = closedAt else { return true }
        return now.timeIntervalSince(c) > ToggleGuard.window
    }
}

/// A transient popover also closes whenever the app is deactivated. macOS deactivates it when the pointer touches the top edge
/// of a display to bring back a menu bar that hides itself (full-screen apps, or "Automatically hide and show the menu bar"):
/// it hands focus back to the app underneath, so the popover vanished just as you reached for the menu bar. We close it
/// ourselves instead: on a click anywhere else, and when the app loses focus anywhere but at the top of a display.
enum Dismissal {
    static let menuBarZone: CGFloat = 44  // taller than the menu bar, so a pointer resting on a revealed bar still counts

    /// Is the pointer at the top of a display, where the menu bar is or is about to appear? (On macOS 26 a display's menu bar
    /// can sit just above its top edge, so the strip reaches a little past it.)
    static func atMenuBar(_ p: NSPoint, frames: [NSRect]) -> Bool {
        frames.contains { p.x >= $0.minX && p.x <= $0.maxX && abs(p.y - $0.maxY) <= menuBarZone }
    }
}

enum Placement {
    static let width: CGFloat = 440
    static let margin: CGFloat = 8

    /// The display a point belongs to: the one containing it, else the nearest (a click in a menu bar that sits above a
    /// display's top edge is not inside any display).
    static func screenIndex(for p: NSPoint, frames: [NSRect]) -> Int {
        func distance(_ r: NSRect) -> CGFloat {
            let dx = max(r.minX - p.x, 0, p.x - r.maxX), dy = max(r.minY - p.y, 0, p.y - r.maxY)
            return (dx * dx + dy * dy).squareRoot()
        }
        return frames.indices.min { distance(frames[$0]) < distance(frames[$1]) } ?? 0
    }

    /// The point to open under: exactly the click's x (so the arrow points at the icon; the popover itself slides to stay
    /// on the display), and y just under the menu bar (the very top of a display that has none). x is only kept inside
    /// the display so the anchor is never off it.
    static func anchor(click p: NSPoint, visible: NSRect, frame: NSRect) -> NSPoint {
        NSPoint(x: min(max(p.x, frame.minX + 4), frame.maxX - 4), y: visible.maxY)
    }
}
