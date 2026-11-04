import Foundation
import CoreServices
import Darwin

final class DesktopMonitor {
    private let queue = DispatchQueue(label: "local.codexdot.monitor", qos: .utility, autoreleaseFrequency: .workItem)
    private let onChange: (DotSnapshot) -> Void
    private let home: URL
    private var fd: Int32 = -1
    private var reader: DispatchSourceRead?
    private var reconciliation: DispatchSourceTimer?
    private var events: FSEventStreamRef?
    private var buffer = FrameBuffer()
    private var receiveBytes = [UInt8](repeating: 0, count: 65536)
    private var reconnectDelay: TimeInterval = 0.5
    private var client = "uninitialized"
    private var connected = false
    private var stopped = false
    private var generation = 0
    private var pending: [String: String] = [:]
    private var owners: [String: String] = [:]
    private var discovering = Set<String>()
    private var answeredDiscovery = Set<String>()
    private var candidates = Set<String>()
    private var excluded = Set<String>()
    private var archived = Set<String>()
    private var archiveChecks: [String: TimeInterval] = [:]
    private var discoveryProblems = Set<String>()
    private var initialSnapshots = Set<String>()
    private var fresh = Set<String>()
    private var subscribed = Set<String>()
    private var snapshotDeadlines: [String: UUID] = [:]
    private var idleLeases: [String: UUID] = [:]
    private var conversations: [String: Conversation] = [:]
    private var checkedAt: [String: TimeInterval] = [:]
    private var retryAt: [String: TimeInterval] = [:]
    private var negativeDiscoveryUntil: [String: TimeInterval] = [:]
    private var readAcknowledgements: [String: TimeInterval] = [:]
    private var catalogUnavailable = false
    private var lastPresentation = ""

    init(onChange: @escaping (DotSnapshot) -> Void) {
        self.onChange = onChange
        home = ProcessInfo.processInfo.environment["CODEX_HOME"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex")
    }
    func start() { queue.async { self.watchFiles(); self.connect() } }
    func stop() {
        queue.sync {
            stopped = true
            if let events { FSEventStreamStop(events); FSEventStreamInvalidate(events); FSEventStreamRelease(events); self.events = nil }
            closeConnection()
        }
    }
    func clearCompleted() {
        queue.async {
            for id in self.conversations.keys where self.conversations[id]?.phase == .ready { self.conversations[id]?.suppressReady() }
            self.publish()
        }
    }
    private func publish(_ message: String? = nil) {
        let sessions = Set(conversations.keys).subtracting(archived).subtracting(excluded).map { id -> DotSession in
            let c = conversations[id]
            return DotSession(id: id, title: c?.title ?? "Checking Codex session…", phase: fresh.contains(id) ? c?.phase ?? .unknown : .unknown)
        }.sorted { $0.id < $1.id }
        let incomplete = catalogUnavailable || !discoveryProblems.isEmpty || sessions.contains { $0.phase == .unknown }
        let message = message ?? (incomplete ? "Some Codex statuses are unavailable. Checking…" :
            owners.keys.contains { conversations[$0] == nil } || !discovering.subtracting(answeredDiscovery).isEmpty ? "Checking desktop sessions…" : nil)
        let fingerprint = "\(connected)|\(message ?? "")|" + sessions.map { "\($0.id):\($0.phase.rawValue):\($0.title)" }.joined(separator: "|")
        guard fingerprint != lastPresentation else { return }
        lastPresentation = fingerprint
        let snapshot = DotSnapshot(connected: connected, sessions: connected ? sessions : [], message: message)
        DispatchQueue.main.async { self.onChange(snapshot) }
    }
    private func connect() {
        guard !stopped, fd == -1 else { return }
        let path = home.appendingPathComponent("ipc/ipc.sock").path
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8) + [0]
        guard bytes.count <= MemoryLayout.size(ofValue: address.sun_path) else {
            publish("Codex connection path is too long."); return
        }
        withUnsafeMutableBytes(of: &address.sun_path) { $0.copyBytes(from: bytes) }
        let socketFD = socket(AF_UNIX, SOCK_STREAM, 0)
        guard socketFD >= 0 else { failed("Cannot open the local Codex connection."); return }
        var noSignal: Int32 = 1
        setsockopt(socketFD, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout.size(ofValue: noSignal)))
        var timeout = timeval(tv_sec: 1, tv_usec: 0)
        setsockopt(socketFD, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout)))
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(socketFD, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard result == 0 else { Darwin.close(socketFD); failed("Open Codex to connect."); return }
        fd = socketFD; buffer = FrameBuffer(); client = "uninitialized"; generation += 1
        let currentGeneration = generation
        let source = DispatchSource.makeReadSource(fileDescriptor: socketFD, queue: queue)
        source.setEventHandler { [weak self] in self?.read() }
        source.setCancelHandler { Darwin.close(socketFD) }
        reader = source; source.resume()
        request("initialize", params: ["clientType": "codex_dot"], version: 0)
        queue.asyncAfter(deadline: .now() + 5) {
            if self.generation == currentGeneration && !self.connected { self.failed("Codex did not finish connecting.") }
        }
    }
    private func closeConnection() {
        reconciliation?.cancel(); reconciliation = nil
        if fd >= 0 && client != "uninitialized" {
            for (id, owner) in owners { following(id, owner: owner, value: false) }
        }
        reader?.cancel(); reader = nil; fd = -1
        connected = false; client = "uninitialized"; generation += 1
        for id in conversations.keys { conversations[id]?.forgetReadAcknowledgement() }
        pending.removeAll(); owners.removeAll(); discovering.removeAll(); fresh.removeAll()
        subscribed.removeAll(); snapshotDeadlines.removeAll(); idleLeases.removeAll()
        initialSnapshots.removeAll(); checkedAt.removeAll(); retryAt.removeAll(); negativeDiscoveryUntil.removeAll(); discoveryProblems.removeAll(); answeredDiscovery.removeAll()
        archived.removeAll(); archiveChecks.removeAll(); readAcknowledgements.removeAll()
    }
    private func failed(_ message: String) {
        closeConnection(); publish(message)
        let attempt = generation
        let delay = reconnectDelay
        reconnectDelay = min(reconnectDelay * 2, 5)
        queue.asyncAfter(deadline: .now() + delay) {
            if !self.stopped && self.generation == attempt { self.connect() }
        }
    }
    @discardableResult private func send(_ object: [String: Any]) -> Bool {
        guard fd >= 0, let data = try? FrameBuffer.encode(object) else { return false }
        return data.withUnsafeBytes { raw in
            var offset = 0
            while offset < raw.count {
                let n = Darwin.send(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset, 0)
                if n < 0 && errno == EINTR { continue }
                guard n > 0 else { return false }
                offset += n
            }
            return true
        }
    }
    private func request(_ method: String, params: [String: Any], version: Int) {
        let id = UUID().uuidString
        pending[id] = method == "initialize" ? method : params["conversationId"] as? String
        guard send(["type": "request", "requestId": id, "sourceClientId": client,
                    "method": method, "version": version, "params": params, "timeoutMs": 3000]) else {
            failed("The Codex connection closed."); return
        }
        queue.asyncAfter(deadline: .now() + 12) {
            if let thread = self.pending.removeValue(forKey: id), thread != "initialize" {
                self.discovering.remove(thread)
                if !self.excluded.contains(thread), !self.archived.contains(thread) { self.discoveryProblems.insert(thread) }
                self.publish()
            }
        }
    }
    private func discover(_ id: String, hinted: Bool = false) {
        guard UUID(uuidString: id) != nil, !excluded.contains(id), !archived.contains(id) else { return }
        candidates.insert(id)
        if hinted { negativeDiscoveryUntil.removeValue(forKey: id) }
        let now = ProcessInfo.processInfo.systemUptime
        guard connected, now >= (retryAt[id] ?? 0), now >= (negativeDiscoveryUntil[id] ?? 0) else { return }
        if let owner = owners[id] {
            if !subscribed.contains(id) || idleLeases[id] != nil { fresh.remove(id); following(id, owner: owner, value: true); publish() }
            return
        }
        guard !discovering.contains(id) else { return }
        discovering.insert(id)
        request("thread-owner-discovery", params: ["conversationId": id, "hostId": "local"], version: 1)
    }
    private func following(_ id: String, owner: String, value: Bool) {
        if value {
            idleLeases.removeValue(forKey: id)
            guard snapshotDeadlines[id] == nil else { return }
            subscribed.insert(id)
            let deadline = UUID()
            snapshotDeadlines[id] = deadline
            queue.asyncAfter(deadline: .now() + 10) {
                if self.snapshotDeadlines[id] == deadline {
                    self.discoveryProblems.insert(id)
                    self.invalidateOwner(id)
                }
            }
        } else {
            subscribed.remove(id); snapshotDeadlines.removeValue(forKey: id); idleLeases.removeValue(forKey: id)
        }
        _ = send(["type": "broadcast", "sourceClientId": client, "targetClientIds": [owner],
                  "method": "thread-stream-following-changed", "version": 1,
                  "params": ["conversationId": id, "hostId": "local", "following": value]])
    }
    private func scanLocks(hinted: Bool = false) {
        let directory = home.appendingPathComponent("thread-writer-locks")
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: directory.path) else { catalogUnavailable = true; return }
        catalogUnavailable = false
        let locks = Set(names.filter { $0.hasSuffix(".lock") }.map { String($0.dropLast(5)) })
        for id in candidates.subtracting(locks) where owners[id] == nil && conversations[id] == nil && !discovering.contains(id) {
            candidates.remove(id); answeredDiscovery.remove(id); discoveryProblems.remove(id)
            retryAt.removeValue(forKey: id); negativeDiscoveryUntil.removeValue(forKey: id); excluded.remove(id)
        }
        for id in locks where owners[id] == nil { discover(id, hinted: hinted) }
    }
    private func startReconciliation() {
        let currentGeneration = generation
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 5, repeating: 5, leeway: .milliseconds(250))
        timer.setEventHandler { [weak self] in
            guard let self, self.connected, self.generation == currentGeneration else { return }
            self.scanLocks()
            let now = ProcessInfo.processInfo.systemUptime
            // Read broadcasts have no revision; bound suppression before re-reading the owner.
            for (id, deadline) in self.readAcknowledgements where now >= deadline {
                self.readAcknowledgements.removeValue(forKey: id)
                if self.conversations[id]?.readTurn != nil {
                    self.conversations[id]?.forgetReadAcknowledgement()
                    if let owner = self.owners[id] { self.following(id, owner: owner, value: true) }
                }
            }
            for (id, check) in self.archiveChecks where now >= check {
                self.archived.remove(id); self.archiveChecks.removeValue(forKey: id)
                self.candidates.insert(id)
            }
            for id in self.candidates where self.owners[id] == nil { self.discover(id) }
            // Final patches and read changes can be missing; silence never proves a state.
            for (id, owner) in self.owners {
                if self.conversations[id]?.phase != .idle || now - (self.checkedAt[id] ?? 0) >= 30 {
                    self.following(id, owner: owner, value: true)
                }
            }
            self.publish()
        }
        reconciliation = timer; timer.resume()
    }
    private func invalidateOwner(_ id: String) {
        if let owner = owners[id] { following(id, owner: owner, value: false) }
        owners.removeValue(forKey: id); fresh.remove(id); initialSnapshots.remove(id)
        snapshotDeadlines.removeValue(forKey: id); idleLeases.removeValue(forKey: id); checkedAt.removeValue(forKey: id)
        conversations[id]?.forgetReadAcknowledgement()
        readAcknowledgements.removeValue(forKey: id)
        retryAt[id] = ProcessInfo.processInfo.systemUptime + 5
        publish()
    }
    private func read() {
        let socketFD = fd
        let n = receiveBytes.withUnsafeMutableBytes { recv(socketFD, $0.baseAddress, $0.count, 0) }
        if n < 0 && errno == EINTR { return }
        guard n > 0 else { failed("Codex disconnected. Waiting to reconnect…"); return }
        do {
            for data in try buffer.append(Data(receiveBytes.prefix(n))) {
                guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw MonitorError.invalidFrame }
                do { try handle(object) }
                catch {
                    guard let params = object["params"] as? [String: Any], params["hostId"] as? String == "local",
                          let id = params["conversationId"] as? String,
                          owners[id] != nil || conversations[id] != nil || discoveryProblems.contains(id) else { throw error }
                    discoveryProblems.insert(id); invalidateOwner(id)
                    publish("A Codex chat changed its status format. Its status is unavailable.")
                }
            }
        } catch { failed("Codex changed its session format. Tracking is unavailable.") }
    }
    private func handle(_ object: [String: Any]) throws {
        let type = object["type"] as? String
        if type == "client-discovery-request", let id = object["requestId"] as? String {
            _ = send(["type": "client-discovery-response", "requestId": id, "clientId": client, "response": ["canHandle": false]])
            return
        }
        if type == "response", let id = object["requestId"] as? String, let requested = pending.removeValue(forKey: id) {
            if requested == "initialize" {
                guard object["resultType"] as? String == "success", let result = object["result"] as? [String: Any], let id = result["clientId"] as? String else { throw MonitorError.incompatibleState }
                client = id; connected = true
                let waiting = candidates
                for id in waiting { discover(id) }
                scanLocks(); startReconciliation(); publish()
                let stableGeneration = generation
                queue.asyncAfter(deadline: .now() + 10) {
                    if self.connected && self.generation == stableGeneration { self.reconnectDelay = 0.5 }
                }
            } else {
                discovering.remove(requested)
                answeredDiscovery.insert(requested)
                guard !excluded.contains(requested), !archived.contains(requested) else { return }
                if object["resultType"] as? String == "success", let owner = object["handledByClientId"] as? String {
                    discoveryProblems.remove(requested); negativeDiscoveryUntil.removeValue(forKey: requested)
                    owners[requested] = owner; initialSnapshots.insert(requested)
                    following(requested, owner: owner, value: true)
                } else {
                    if conversations[requested]?.phase == .idle { conversations.removeValue(forKey: requested) }
                    // Native negatives can arrive on a reconciliation tick; avoid immediately asking again.
                    if object["error"] as? String == "no-client-found", conversations[requested] == nil {
                        negativeDiscoveryUntil[requested] = ProcessInfo.processInfo.systemUptime + 1
                    }
                }
                publish()
            }
            return
        }
        guard type == "broadcast", let method = object["method"] as? String else { return }
        if method == "ipc-connection-reset" { failed("Codex reset its connection. Reconnecting…"); return }
        guard let params = object["params"] as? [String: Any] else { return }
        if method == "client-status-changed", params["status"] as? String == "connected" { scanLocks(hinted: true); return }
        if method == "client-status-changed", params["status"] as? String == "disconnected", let owner = params["clientId"] as? String {
            for (id, value) in owners where value == owner {
                owners.removeValue(forKey: id); fresh.remove(id); subscribed.remove(id)
                snapshotDeadlines.removeValue(forKey: id); idleLeases.removeValue(forKey: id)
                initialSnapshots.remove(id); checkedAt.removeValue(forKey: id)
                conversations[id]?.forgetReadAcknowledgement()
                readAcknowledgements.removeValue(forKey: id)
            }
            publish(); scanLocks(); return
        }
        guard params["hostId"] as? String == "local", let id = params["conversationId"] as? String else { return }
        if method == "thread-read-state-changed" {
            guard conversations[id] != nil, !archived.contains(id), !excluded.contains(id) else { return }
            guard object["version"] as? Int == 3, let unread = params["hasUnreadTurn"] as? Bool else { throw MonitorError.incompatibleState }
            // Read changes are global broadcasts even after an idle stream is released.
            conversations[id]?.applyReadState(unread)
            if unread || conversations[id]?.readTurn == nil { readAcknowledgements.removeValue(forKey: id) }
            else { readAcknowledgements[id] = ProcessInfo.processInfo.systemUptime + 5 }
            publish(); return
        }
        if method == "thread-stream-following-changed", object["sourceClientId"] as? String != client {
            if params["following"] as? Bool == true { discover(id, hinted: true) }
            return
        }
        if method == "thread-archived" {
            if let owner = owners[id] { following(id, owner: owner, value: false) }
            readAcknowledgements.removeValue(forKey: id)
            archived.insert(id); candidates.remove(id); discoveryProblems.remove(id); initialSnapshots.remove(id)
            archiveChecks[id] = ProcessInfo.processInfo.systemUptime + 30
            checkedAt.removeValue(forKey: id)
            owners.removeValue(forKey: id); fresh.remove(id); conversations.removeValue(forKey: id); publish(); return
        }
        if method == "thread-unarchived" {
            guard object["version"] as? Int == 1, UUID(uuidString: id) != nil else { throw MonitorError.incompatibleState }
            archived.remove(id); archiveChecks.removeValue(forKey: id); discover(id); return
        }
        guard method == "thread-stream-state-changed", subscribed.contains(id), let owner = owners[id], object["sourceClientId"] as? String == owner else { return }
        guard object["version"] as? Int == 11, let change = params["change"] as? [String: Any], let revision = change["revision"] as? Int else { throw MonitorError.incompatibleState }
        var presentationChanged = true
        if change["type"] as? String == "snapshot", let state = change["conversationState"] as? [String: Any] {
            guard state["id"] as? String == id else { throw MonitorError.incompatibleState }
            guard Conversation.desktop(state) else {
                conversations[id]?.forgetReadAcknowledgement()
                readAcknowledgements.removeValue(forKey: id)
                following(id, owner: owner, value: false); owners.removeValue(forKey: id); fresh.remove(id)
                initialSnapshots.remove(id); discoveryProblems.remove(id); checkedAt.removeValue(forKey: id)
                excluded.insert(id); conversations.removeValue(forKey: id); publish(); return
            }
            if !initialSnapshots.contains(id), let previous = conversations[id], revision < previous.revision {
                invalidateOwner(id); return
            }
            conversations[id] = try Conversation.snapshot(state, revision: revision, previous: conversations[id])
            snapshotDeadlines.removeValue(forKey: id); initialSnapshots.remove(id); discoveryProblems.remove(id)
            retryAt.removeValue(forKey: id)
            checkedAt[id] = ProcessInfo.processInfo.systemUptime
            fresh.insert(id)
        } else if change["type"] as? String == "patches", let patches = change["patches"] as? [[String: Any]], let base = change["baseRevision"] as? Int {
            guard fresh.contains(id) else { following(id, owner: owner, value: true); return }
            if Conversation.attentionNeedsSnapshot(patches, pending: conversations[id]?.pendingAttention != nil) {
                fresh.remove(id); following(id, owner: owner, value: true); publish(); return
            }
            let previousTitle = conversations[id]?.title, previousPhase = conversations[id]?.phase
            do {
                try conversations[id]?.patches(patches, base: base, revision: revision)
                presentationChanged = previousTitle != conversations[id]?.title || previousPhase != conversations[id]?.phase
            } catch { fresh.remove(id); following(id, owner: owner, value: true) }
        } else { throw MonitorError.incompatibleState }
        releaseWhenIdle(id)
        if presentationChanged { publish() }
    }
    private func releaseWhenIdle(_ id: String) {
        guard let state = conversations[id], fresh.contains(id) else { return }
        if state.phase == .working || state.phase == .waiting || state.phase == .approval {
            idleLeases.removeValue(forKey: id); return
        }
        guard idleLeases[id] == nil else { return }
        // Release terminal histories after a short settling period. File events reattach on the next turn.
        let lease = UUID(); idleLeases[id] = lease
        queue.asyncAfter(deadline: .now() + 10) {
            guard self.idleLeases[id] == lease, let owner = self.owners[id] else { return }
            self.following(id, owner: owner, value: false)
        }
    }
    private func watchFiles() {
        // Filenames discover new/background sessions; only a desktop IPC snapshot establishes state.
        var context = FSEventStreamContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(), retain: nil, release: nil, copyDescription: nil)
        let paths = [home.appendingPathComponent("thread-writer-locks").path, home.appendingPathComponent("sessions").path] as CFArray
        events = FSEventStreamCreate(nil, { _, info, count, paths, flags, _ in
            guard let info else { return }
            let monitor = Unmanaged<DesktopMonitor>.fromOpaque(info).takeUnretainedValue()
            let changed = unsafeBitCast(paths, to: NSArray.self) as? [String] ?? []
            for (index, path) in changed.prefix(count).enumerated() {
                if flags[index] & FSEventStreamEventFlags(kFSEventStreamEventFlagMustScanSubDirs | kFSEventStreamEventFlagUserDropped | kFSEventStreamEventFlagKernelDropped) != 0 { monitor.scanLocks(hinted: true) }
                let name = URL(fileURLWithPath: path).lastPathComponent
                if name.hasSuffix(".jsonl") { monitor.discover(String(name.dropLast(6).suffix(36)), hinted: true) }
                else if name.hasSuffix(".lock") { monitor.discover(String(name.dropLast(5)), hinted: true) }
                else if path.hasSuffix("thread-writer-locks") { monitor.scanLocks(hinted: true) }
            }
        }, &context, paths, FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 0.5,
        FSEventStreamCreateFlags(kFSEventStreamCreateFlagUseCFTypes | kFSEventStreamCreateFlagFileEvents))
        if let events { FSEventStreamSetDispatchQueue(events, queue); FSEventStreamStart(events) }
    }
}

func runCommandLineIfRequested() -> Bool {
    guard CommandLine.arguments.contains("--probe") else { return false }
    let seconds = CommandLine.arguments.drop { $0 != "--probe" }.dropFirst().first.flatMap(Double.init) ?? 15
    var monitor: DesktopMonitor!
    monitor = DesktopMonitor { snapshot in
        let output: [String: Any] = ["connected": snapshot.connected,
            "sessions": snapshot.sessions.map { ["id": $0.id, "phase": $0.phase.rawValue] }, "message": snapshot.message ?? ""]
        if let data = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]), let line = String(data: data, encoding: .utf8) {
            print(line); fflush(stdout)
        }
    }
    monitor.start()
    let deadline = Date().addingTimeInterval(min(max(seconds, 1), 300))
    while Date() < deadline { RunLoop.main.run(until: min(deadline, Date().addingTimeInterval(0.2))) }
    monitor.stop()
    return true
}
