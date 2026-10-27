// Where the popover opens. On macOS 26 the system hosts a menu bar item's window itself and gives it no screen at all
// (on a second display its frame even sits above the display's top edge), so anchoring the popover to the item puts it in
// the wrong place. We anchor under the click instead: the display the click was on, the click's x, just under the menu bar.
import AppKit

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
