import Foundation
import Darwin

let catalog = LocalThreadCatalog(home: URL(fileURLWithPath: CommandLine.arguments[1]))
while let line = readLine() {
    let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
    guard parts.count == 2 else { fatalError("Expected operation and chat ID") }
    switch parts[0] {
    case "remember": catalog.remember(parts[1]); print("null")
    case "orphan": print(catalog.isOrphan(parts[1]) ? "true" : "false")
    case "forget": catalog.forget(parts[1]); print("null")
    default: fatalError("Unknown operation")
    }
    fflush(stdout)
}
