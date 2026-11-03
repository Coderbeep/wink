let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
func check(_ condition: Bool, _ message: String) { if !condition { fatalError(message) } }
private let indicator = WorkingIndicator(frame: NSRect(x: 0, y: 0, width: 12, height: 12))
indicator.layoutSubtreeIfNeeded()
indicator.updateLayer()
check(indicator.glyph.anchorPoint == CGPoint(x: 0.5, y: 0.5), "rotation has a centered anchor")
check(indicator.glyph.position == CGPoint(x: 6, y: 6), "rotation stays inside its image slot")
check(indicator.hitTest(NSPoint(x: 6, y: 6)) == nil, "icon must pass menu clicks through")
indicator.update(working: true, reduceMotion: false)
let rotation = indicator.glyph.animation(forKey: "rotation") as! CABasicAnimation
check(rotation.keyPath == "transform.rotation.z" && rotation.duration == 1.8 && rotation.repeatCount == .infinity, "continuous rotation")
check(rotation.toValue as? Double == -Double.pi * 2, "one full turn in the reversed direction")
let retained = CABasicAnimation(keyPath: "transform.rotation.z")
retained.duration = 9
indicator.glyph.add(retained, forKey: "rotation")
indicator.update(working: true, reduceMotion: false)
check(indicator.glyph.animation(forKey: "rotation")?.duration == 9, "count updates must not restart animation")
indicator.update(working: true, reduceMotion: true)
check(!indicator.isHidden && indicator.glyph.animationKeys() == nil, "Reduce Motion keeps a static working glyph")
indicator.update(working: false, reduceMotion: false)
check(indicator.isHidden && indicator.glyph.animationKeys() == nil, "idle/disconnected has no animation")

let snapshot = DotSnapshot(connected: true, sessions: [DotSession(id: "work", title: "Work", phase: .working), DotSession(id: "answer", title: "Answer", phase: .ready)], message: nil)
let title = statusTitle(snapshot)
check(title.string.filter { $0 == "1" }.count == 2, "working and ready counts are retained")
var attachments = 0
title.enumerateAttribute(.attachment, in: NSRange(location: 0, length: title.length)) { value, _, _ in if value != nil { attachments += 1 } }
check(attachments == 1, "only working glyph moves to the native image slot")

let ring = NSBitmapImageRep(data: statusImage(.working)!.tiffRepresentation!)!
var quadrants = [Double](repeating: 0, count: 4)
for y in 0..<ring.pixelsHigh {
    for x in 0..<ring.pixelsWide {
        let quadrant = (x < ring.pixelsWide / 2 ? 0 : 1) + (y < ring.pixelsHigh / 2 ? 0 : 2)
        quadrants[quadrant] += ring.colorAt(x: x, y: y)!.alphaComponent
    }
}
let inkCoverage = quadrants.reduce(0, +) / Double(ring.pixelsWide * ring.pixelsHigh)
check((0.04...0.16).contains(inkCoverage), "fine working marks remain visible without becoming heavy")
check(quadrants.min()! > quadrants.max()! * 0.55, "marks fill every quadrant without an open sector")

if let output = CommandLine.arguments.dropFirst().first {
    let preview = NSImage(size: NSSize(width: 480, height: 120))
    preview.lockFocus()
    for (row, appearance) in [NSAppearance.Name.aqua, .darkAqua].enumerated() {
        let area = NSRect(x: 0, y: row * 60, width: 480, height: 60)
        let dark = appearance == .darkAqua
        (dark ? NSColor(calibratedWhite: 0.13, alpha: 1) : .white).setFill()
        area.fill()
        NSAppearance(named: appearance)!.performAsCurrentDrawingAppearance {
            for (index, degrees) in [0.0, -9, -18, -27].enumerated() {
                let image = statusImage(.working)!
                NSGraphicsContext.saveGraphicsState()
                let transform = NSAffineTransform()
                transform.translateX(by: CGFloat(index * 120 + 30), yBy: CGFloat(row * 60 + 30))
                transform.rotate(byDegrees: degrees)
                transform.concat()
                image.draw(in: NSRect(x: -6, y: -6, width: 12, height: 12))
                NSGraphicsContext.restoreGraphicsState()
                let text = NSAttributedString(string: "2", attributes: [.font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .regular), .foregroundColor: NSColor.labelColor])
                text.draw(at: NSPoint(x: index * 120 + 43, y: row * 60 + 22))
            }
        }
    }
    preview.unlockFocus()
    let bitmap = NSBitmapImageRep(data: preview.tiffRepresentation!)!
    try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
}
for (status, state) in [(SMAppService.Status.notRegistered, NSControl.StateValue.off), (.enabled, .on), (.requiresApproval, .mixed), (.notFound, .off)] {
    let item = launchAtLoginMenuItem(status: status)
    check(item.state == state && item.isEnabled, "login setting reflects the system status")
    check(item.title.contains("Needs Approval") == (status == .requiresApproval), "approval is explicit, never shown as enabled")
}
for status in [SMAppService.Status.notRegistered, .enabled, .requiresApproval, .notFound] {
    var registrations = 0, removals = 0
    try changeLaunchAtLogin(status: status, register: { registrations += 1 }, unregister: { removals += 1 })
    let removing = status == .enabled || status == .requiresApproval
    check(registrations == (removing ? 0 : 1) && removals == (removing ? 1 : 0), "each system state selects exactly one correct login operation")
}
let denied = NSError(domain: "WinkLoginTest", code: 42)
for status in [SMAppService.Status.notRegistered, .enabled, .requiresApproval] {
    do {
        try changeLaunchAtLogin(status: status, register: { throw denied }, unregister: { throw denied })
        fatalError("login operation errors must reach the UI")
    } catch { check((error as NSError) == denied, "the original login error is preserved") }
}
print("Launch at Login checks passed: system states, pending approval, register/unregister, errors; no login settings changed")
print("Working rotation checks passed: centered, click-through, continuous, stable counts, stop/resume, Reduce Motion")
