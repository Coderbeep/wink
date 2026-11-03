import Foundation

let target = "10000000-0000-4000-8000-000000000002"
var sawWorking = false
var cleared = false
var monitor: DesktopMonitor!
monitor = DesktopMonitor { snapshot in
    let phase = snapshot.sessions.first { $0.id == target }?.phase
    if phase == .working { sawWorking = true }
    if sawWorking && !cleared && phase == .ready {
        cleared = true
        monitor.clearCompleted()
    }
    let output: [String: Any] = ["connected": snapshot.connected,
        "sessions": snapshot.sessions.map { ["id": $0.id, "phase": $0.phase.rawValue] }, "message": snapshot.message ?? ""]
    if let data = try? JSONSerialization.data(withJSONObject: output), let line = String(data: data, encoding: .utf8) {
        print(line); fflush(stdout)
    }
}
monitor.start()
let deadline = Date().addingTimeInterval(11)
while Date() < deadline { RunLoop.main.run(until: min(deadline, Date().addingTimeInterval(0.1))) }
monitor.stop()
