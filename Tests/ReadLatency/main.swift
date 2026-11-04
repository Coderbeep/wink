import Foundation
import Darwin

let monitor = DesktopMonitor { snapshot in
    let output: [String: Any] = ["connected": snapshot.connected,
        "sessions": snapshot.sessions.map { ["id": $0.id, "phase": $0.phase.rawValue] },
        "message": snapshot.message ?? ""]
    if let data = try? JSONSerialization.data(withJSONObject: output), let line = String(data: data, encoding: .utf8) {
        print(line); fflush(stdout)
    }
}
monitor.start()
monitor.setCodexFrontmost(true)
DispatchQueue.global(qos: .utility).async {
    while let command = readLine() {
        switch command {
        case "foreground": monitor.setCodexFrontmost(true)
        case "background": monitor.setCodexFrontmost(false)
        case "refresh": monitor.refreshReady()
        default: break
        }
    }
}
let duration = CommandLine.arguments.last.flatMap(TimeInterval.init) ?? 5
let deadline = Date().addingTimeInterval(duration)
while Date() < deadline { RunLoop.main.run(until: min(deadline, Date().addingTimeInterval(0.2))) }
monitor.stop()
