import AppKit
import QuartzCore

private func adaptiveColor(_ light: UInt32, _ dark: UInt32) -> NSColor {
    NSColor(name: nil) { appearance in
        let hex = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
        return NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255,
                       green: CGFloat((hex >> 8) & 255) / 255,
                       blue: CGFloat(hex & 255) / 255, alpha: 1)
    }
}

private func statusStyle(_ phase: SessionPhase) -> (symbol: String, label: String, color: NSColor) {
    // Codex desktop defaults: neutral activity, blue answers/input, green approval, red failure.
    switch phase {
    case .working: return ("circle.dotted", "Working", .labelColor.withAlphaComponent(0.75))
    case .ready: return ("checkmark.circle", "Answer ready", adaptiveColor(0x339CFF, 0x99CEFF))
    case .waiting: return ("questionmark.bubble", "Needs input", adaptiveColor(0x339CFF, 0x339CFF))
    case .approval: return ("hand.raised.fill", "Needs approval", adaptiveColor(0x00A240, 0x40C977))
    case .failed: return ("xmark.circle", "Failed", adaptiveColor(0xE02E2A, 0xFF6764))
    case .unknown: return ("questionmark.circle", "Status unavailable", .secondaryLabelColor)
    case .idle: return ("circle", "Idle", .secondaryLabelColor)
    }
}

private func statusImage(_ phase: SessionPhase, size: CGFloat = 12) -> NSImage? {
    let style = statusStyle(phase)
    if phase == .working {
        let color = style.color.usingColorSpace(.sRGB) ?? style.color
        return NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
            color.setStroke()
            for index in 0..<10 {
                let arc = NSBezierPath()
                arc.lineWidth = size / 12
                arc.lineCapStyle = .round
                arc.appendArc(withCenter: NSPoint(x: rect.midX, y: rect.midY), radius: size * (4.6 / 12),
                              startAngle: CGFloat(index * 36), endAngle: CGFloat(index * 36 + 14))
                arc.stroke()
            }
            return true
        }
    }
    let configuration = NSImage.SymbolConfiguration(pointSize: size, weight: .medium)
        .applying(NSImage.SymbolConfiguration(paletteColors: [style.color]))
    guard let image = NSImage(systemSymbolName: style.symbol, accessibilityDescription: style.label)?
        .withSymbolConfiguration(configuration) else { return nil }
    image.isTemplate = false
    return image
}

private let visiblePhases: [SessionPhase] = [.working, .ready, .waiting, .approval, .failed, .unknown]

private final class WorkingIndicator: NSView {
    let glyph = CALayer()

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.addSublayer(glyph)
        setAccessibilityElement(false)
    }
    required init?(coder: NSCoder) { nil }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            glyph.contents = statusImage(.working)
        }
        glyph.contentsScale = window?.backingScaleFactor ?? 2
        glyph.contentsGravity = .resizeAspect
    }
    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        glyph.frame = bounds
        CATransaction.commit()
    }
    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }
    func update(working: Bool, reduceMotion: Bool) {
        isHidden = !working
        if !working || reduceMotion { glyph.removeAnimation(forKey: "rotation"); return }
        guard glyph.animation(forKey: "rotation") == nil else { return }
        let rotation = CABasicAnimation(keyPath: "transform.rotation.z")
        rotation.fromValue = 0
        rotation.toValue = -Double.pi * 2
        rotation.duration = 1.8
        rotation.repeatCount = .infinity
        rotation.timingFunction = CAMediaTimingFunction(name: .linear)
        glyph.add(rotation, forKey: "rotation")
    }
}

private func statusTitle(_ snapshot: DotSnapshot) -> NSAttributedString {
    let title = NSMutableAttributedString(string: "")
    if snapshot.connected {
        for phase in visiblePhases {
            let count = snapshot.sessions.filter { $0.phase == phase }.count
            guard count > 0 else { continue }
            if title.length > 0 { title.append(NSAttributedString(string: "   ")) }
            if phase != .working, let image = statusImage(phase) {
                let attachment = NSTextAttachment()
                attachment.image = image
                attachment.bounds = NSRect(x: 0, y: -2, width: 12, height: 12)
                title.append(NSAttributedString(attachment: attachment))
            }
            title.append(NSAttributedString(string: " \(count)"))
        }
    }
    if title.length == 0 { title.append(NSAttributedString(string: snapshot.connected ? "Wink" : "Wink ?")) }
    if snapshot.connected && snapshot.message != nil && !snapshot.sessions.contains(where: { $0.phase == .unknown }) {
        title.append(NSAttributedString(string: " ?"))
    }
    title.addAttributes([.font: NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .regular),
                         .foregroundColor: NSColor.labelColor], range: NSRange(location: 0, length: title.length))
    return title
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private var snapshot = DotSnapshot(connected: false, sessions: [], message: "Connecting to Codex…")
    private var monitor: DesktopMonitor!
    private var menuIsOpen = false
    private let workingIndicator = WorkingIndicator(frame: .zero)
    private let workingPlaceholder = NSImage(size: NSSize(width: 12, height: 12))
    private var hasWorkingSessions: Bool { snapshot.connected && snapshot.sessions.contains { $0.phase == .working } }

    func applicationDidFinishLaunching(_ notification: Notification) {
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
        if let button = statusItem.button {
            button.addSubview(workingIndicator)
            button.postsFrameChangedNotifications = true
            NotificationCenter.default.addObserver(self, selector: #selector(positionWorkingIndicator(_:)), name: NSView.frameDidChangeNotification, object: button)
        }
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(updateWorkingAnimation(_:)), name: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification, object: nil)
        monitor = DesktopMonitor { [weak self] snapshot in
            self?.snapshot = snapshot
            self?.updateStatus()
        }
        updateStatus()
        monitor.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        workingIndicator.update(working: false, reduceMotion: false)
        NotificationCenter.default.removeObserver(self)
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        monitor?.stop()
    }

    private func updateStatus() {
        guard let button = statusItem.button else { return }
        let working = hasWorkingSessions
        button.image = working ? workingPlaceholder : nil
        button.imagePosition = working ? .imageLeft : .noImage
        button.attributedTitle = statusTitle(snapshot)
        updateWorkingAnimation()
        positionWorkingIndicator()
        if snapshot.connected {
            let counts = visiblePhases.compactMap { phase in
                let count = snapshot.sessions.filter { $0.phase == phase }.count
                return count > 0 ? "\(count) \(statusStyle(phase).label.lowercased())" : nil
            }.joined(separator: ", ")
            button.toolTip = [snapshot.message, counts.isEmpty ? nil : counts].compactMap { $0 }.joined(separator: ". ")
            if button.toolTip?.isEmpty == true { button.toolTip = "No active conversations" }
        } else { button.toolTip = snapshot.message ?? "Codex is disconnected" }
        button.setAccessibilityLabel("Wink. " + (button.toolTip ?? ""))
        if menuIsOpen { rebuildMenu() }
    }

    @objc private func positionWorkingIndicator(_ notification: Notification? = nil) {
        guard let button = statusItem.button, !workingIndicator.isHidden,
              let frame = button.cell?.imageRect(forBounds: button.bounds) else { return }
        workingIndicator.frame = frame
    }

    @objc private func updateWorkingAnimation(_ notification: Notification? = nil) {
        workingIndicator.update(working: hasWorkingSessions, reduceMotion: NSWorkspace.shared.accessibilityDisplayShouldReduceMotion)
    }

    func menuWillOpen(_ menu: NSMenu) {
        menuIsOpen = true
        rebuildMenu()
    }

    func menuDidClose(_ menu: NSMenu) { menuIsOpen = false }

    private func rebuildMenu() {
        menu.removeAllItems()
        addLabel("Wink")
        if !snapshot.connected { addLabel(snapshot.message ?? "Codex is disconnected") }
        else {
            if let message = snapshot.message { addLabel(message) }
            let rank: [SessionPhase: Int] = [.approval: 0, .waiting: 1, .failed: 2, .unknown: 3, .working: 4, .ready: 5]
            let sessions = snapshot.sessions.filter { rank[$0.phase] != nil }.sorted {
                let lhs = rank[$0.phase]!, rhs = rank[$1.phase]!
                return lhs == rhs ? $0.id < $1.id : lhs < rhs
            }
            if sessions.isEmpty && snapshot.message == nil { addLabel("No active conversations") }
            for session in sessions.prefix(15) {
                addLabel("\(statusStyle(session.phase).label) — \(displayTitle(session.title))", phase: session.phase)
            }
            if sessions.count > 15 { addLabel("\(sessions.count - 15) more conversations") }
        }
        menu.addItem(.separator())
        addAction("Open Codex", #selector(openCodex))
        let clear = addAction("Clear completed", #selector(clearCompleted))
        clear.isEnabled = snapshot.connected && snapshot.sessions.contains { $0.phase == .ready }
        menu.addItem(.separator())
        addAction("Quit Wink", #selector(quit), key: "q")
    }

    private func displayTitle(_ title: String) -> String {
        let clean = title.components(separatedBy: .controlCharacters).joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if clean.isEmpty { return "Untitled conversation" }
        return String(clean.prefix(60)) + (clean.count > 60 ? "…" : "")
    }

    private func addLabel(_ text: String, phase: SessionPhase? = nil) {
        let item = NSMenuItem(title: text, action: nil, keyEquivalent: "")
        if let phase {
            item.image = statusImage(phase, size: 13)
            item.attributedTitle = NSAttributedString(string: text, attributes: [.foregroundColor: NSColor.labelColor])
        }
        item.isEnabled = false
        menu.addItem(item)
    }

    @discardableResult
    private func addAction(_ title: String, _ action: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        menu.addItem(item)
        return item
    }

    @objc private func openCodex() {
        if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.openai.codex") {
            NSWorkspace.shared.openApplication(at: url, configuration: .init())
        }
    }

    @objc private func clearCompleted() { monitor.clearCompleted() }
    @objc private func quit() { NSApplication.shared.terminate(nil) }
}

if !runCommandLineIfRequested() {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.setActivationPolicy(.accessory)
    app.delegate = delegate
    withExtendedLifetime(delegate) { app.run() }
}
