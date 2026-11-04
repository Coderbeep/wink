import Foundation

enum SessionPhase: String { case working, waiting, approval, ready, idle, failed, unknown }
struct DotSession { var id: String; var title: String; var phase: SessionPhase }
struct DotSnapshot { var connected: Bool; var sessions: [DotSession]; var message: String? }

/// The desktop IPC is local and length-prefixed; never allocate from an unchecked frame length.
struct FrameBuffer {
    static let limit = 16 * 1024 * 1024
    private var bytes = Data()
    mutating func append(_ chunk: Data) throws -> [Data] {
        bytes.append(chunk)
        var frames: [Data] = []
        var offset = 0
        while bytes.count - offset >= 4 {
            let length = (0..<4).reduce(0) { $0 | (Int(bytes[offset + $1]) << (8 * $1)) }
            guard length > 0, length <= Self.limit else { throw MonitorError.invalidFrame }
            guard bytes.count - offset - 4 >= length else { break }
            frames.append(bytes.subdata(in: (offset + 4)..<(offset + 4 + length)))
            offset += 4 + length
        }
        if offset > 0 { bytes = Data(bytes.dropFirst(offset)) }
        guard bytes.count <= Self.limit + 4 else { throw MonitorError.invalidFrame }
        return frames
    }
    static func encode(_ message: [String: Any]) throws -> Data {
        let body = try JSONSerialization.data(withJSONObject: message)
        guard body.count <= limit else { throw MonitorError.invalidFrame }
        let count = UInt32(body.count)
        var result = Data((0..<4).map { UInt8(truncatingIfNeeded: count >> (8 * $0)) })
        result.append(body)
        return result
    }
}

enum MonitorError: Error { case invalidFrame, incompatibleState, disconnected }

struct TurnMetadata {
    var id: String?
    var status: String?
    var terminalError: Bool
    init(_ value: [String: Any]) {
        id = value["turnId"] as? String
        status = value["status"] as? String
        terminalError = (value["items"] as? [[String: Any]] ?? []).contains {
            $0["type"] as? String == "error" && $0["willRetry"] as? Bool == false
                && $0.keys.contains("message") && $0.keys.contains("errorInfo")
        }
    }
    mutating func update(_ field: String, value: Any?) {
        switch field {
        case "turnId": id = value as? String
        case "status": status = value as? String
        default: break
        }
    }
}

enum PendingRequestKind { case input, approval, form, other, unavailable }

struct Conversation {
    var id: String
    var title: String
    var runtime: [String: Any]
    var unread: Bool
    var turns: [String: TurnMetadata]
    var turnOrder: [String]
    var latestEntityKey: String?
    var revision: Int
    var readyTurn: String?
    var readTurn: String?
    var nativeRequest: PendingRequestKind?
    var hasApprovalRequest: Bool
    var asyncInput: Bool
    var resumeState: String?

    var latest: TurnMetadata? { latestEntityKey.flatMap { turns[$0] } }
    var control: TurnMetadata? {
        turnOrder.reversed().lazy.compactMap { turns[$0] }.first {
            $0.id != nil || $0.status != "completed" && $0.status != "failed"
        }
    }
    var currentKey: String { latest?.id ?? latestEntityKey ?? "empty:\(id)" }
    var pendingAttention: SessionPhase? {
        let active = runtime["type"] as? String == "active"
        let flags = runtime["activeFlags"] as? [String] ?? []
        if resumeState != nil {
            if nativeRequest == .unavailable {
                if resumeState != "needs_resume" { return .unknown }
                if active && flags.contains("waitingOnUserInput") { return .waiting }
                if active && flags.contains("waitingOnApproval") && hasApprovalRequest { return .unknown }
            } else if nativeRequest == .form {
                if resumeState != "needs_resume" || active && (flags.contains("waitingOnUserInput") || flags.contains("waitingOnApproval") && hasApprovalRequest) { return .waiting }
            } else if resumeState == "needs_resume" {
                if active && flags.contains("waitingOnApproval") && hasApprovalRequest { return .approval }
                if active && flags.contains("waitingOnUserInput") { return .waiting }
            } else {
                if nativeRequest == .input { return .waiting }
                if nativeRequest == .approval { return .approval }
            }
        }
        return asyncInput ? .waiting : nil
    }
    var phase: SessionPhase {
        if let pendingAttention { return pendingAttention }
        switch runtime["type"] as? String {
        case "active": return .working
        case "systemError": return .failed
        case "idle":
            if resumeState != "needs_resume" {
                if latest?.terminalError == true { return .failed }
                if control?.status == "inProgress" { return .working }
                if let status = control?.status, !["completed", "failed", "interrupted"].contains(status) { return .unknown }
            }
            return unread && readyTurn == currentKey ? .ready : .idle
        default: return .unknown
        }
    }

    static func desktop(_ state: [String: Any]) -> Bool {
        let origin = state["originator"] as? String
        return ["Codex Desktop", "codex_work_desktop"].contains(origin ?? "")
            && state["source"] as? String == "vscode"
            && (state["threadSource"] == nil || state["threadSource"] as? String == "user")
            && state["sideConversation"] as? Bool != true
            && state["ephemeral"] as? Bool != true
            && (state["parentThreadId"] == nil || state["parentThreadId"] is NSNull)
    }
    static func orderedTurns(_ state: [String: Any]) throws -> [(String, [String: Any])] {
        if let normalized = state["turnHistory"] as? [String: Any] {
            guard normalized["kind"] as? String == "canonical",
                  let history = normalized["history"] as? [String: Any],
                  let entities = history["entitiesByKey"] as? [String: Any],
                  let islands = history["islands"] as? [[String: Any]] else { throw MonitorError.incompatibleState }
            var result: [(String, [String: Any])] = []
            var seen = Set<String>()
            for island in islands {
                guard let entries = island["entries"] as? [[String: Any]] else { throw MonitorError.incompatibleState }
                for entry in entries {
                    guard let key = entry["value"] as? String, seen.insert(key).inserted,
                          let value = entities[key] as? [String: Any] else { throw MonitorError.incompatibleState }
                    result.append((key, value))
                }
            }
            return result
        }
        return (state["turns"] as? [[String: Any]] ?? []).enumerated().map { ("legacy:\($0.offset)", $0.element) }
    }
    static func snapshot(_ state: [String: Any], revision: Int, previous: Conversation?) throws -> Conversation {
        guard let id = state["id"] as? String,
              let runtime = state["threadRuntimeStatus"] as? [String: Any],
              runtime["type"] is String,
              let unread = state["hasUnreadTurn"] as? Bool else { throw MonitorError.incompatibleState }
        let ordered = try orderedTurns(state)
        var latestKey = ordered.last?.0
        if let history = (state["turnHistory"] as? [String: Any])?["history"] as? [String: Any],
           let newest = (history["islands"] as? [[String: Any]])?.last,
           (newest["newerBoundary"] as? [String: Any])?["status"] as? String == "exhausted" {
            latestKey = (newest["entries"] as? [[String: Any]])?.last?["value"] as? String
        }
        let requests = state["requests"] as? [[String: Any]] ?? []
        var result = Conversation(id: id, title: state["title"] as? String ?? "Codex conversation",
                                  runtime: runtime, unread: unread,
                                  turns: Dictionary(uniqueKeysWithValues: ordered.map { ($0.0, TurnMetadata($0.1)) }), turnOrder: ordered.map { $0.0 }, latestEntityKey: latestKey,
                                  revision: revision, readyTurn: previous?.readyTurn, readTurn: previous?.readTurn,
                                  nativeRequest: nativeAttention(ordered.map { $0.1 }, requests: requests),
                                  hasApprovalRequest: requests.contains { request in
                                      let method = request["method"] as? String ?? ""
                                      return ["item/commandExecution/requestApproval", "item/fileChange/requestApproval", "item/permissions/requestApproval"].contains(method)
                                          || method == "mcpServer/elicitation/request" && request["completed"] as? Bool != true && elicitation(request["params"] as? [String: Any] ?? [:]) != nil
                                  }, asyncInput: unansweredQuestions(ordered.map { $0.1 }), resumeState: state["resumeState"] as? String)
        if !result.unread { result.readTurn = nil }
        result.refreshReady(previousKey: previous?.currentKey, previousStatus: previous?.latest?.status, initial: previous == nil, becameUnread: previous?.unread == false && result.unread)
        return result
    }
    static func replyIDs(_ original: String) -> Set<String> {
        var text = original.components(separatedBy: "\n## My request:\n").last!.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("[from:"), let line = text.firstIndex(of: "\n"), text[..<line].hasSuffix("]") {
            text = String(text[text.index(after: line)...]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if text.hasPrefix("<codex_multi_user_message>"), text.hasSuffix("</codex_multi_user_message>"),
           let body = text.range(of: "<body>"), let end = text.range(of: "</body>", range: body.upperBound..<text.endIndex) {
            text = String(text[body.upperBound..<end.lowerBound]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let start = "<send_user_message_question_reply>", end = "</send_user_message_question_reply>"
        guard text.hasPrefix(start), text.hasSuffix(end) else { return [] }
        let json = text.dropFirst(start.count).dropLast(end.count).trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = json.data(using: .utf8), let decoded = try? JSONSerialization.jsonObject(with: data) else { return [] }
        let replies: [[String: Any]]
        if let array = decoded as? [[String: Any]] { replies = array }
        else if let one = decoded as? [String: Any] { replies = [one] }
        else { return [] }
        guard !replies.isEmpty, replies.allSatisfy({ $0["questionItemId"] is String && $0["question"] is String && $0["answer"] is String }) else { return [] }
        return Set(replies.compactMap { $0["questionItemId"] as? String })
    }
    static func validText(_ value: Any?, max: Int) -> Bool {
        guard let text = value as? String else { return false }
        let count = text.trimmingCharacters(in: .whitespacesAndNewlines).utf16.count
        return count > 0 && count <= max
    }
    static func validPersist(_ value: Any?) -> Bool {
        if let value = value as? String { return ["session", "always"].contains(value) }
        guard let values = value as? [String], !values.isEmpty, values.count <= 2 else { return false }
        return values.allSatisfy { ["session", "always"].contains($0) }
    }
    static func validEnvironmentInput(_ value: Any?) -> Bool {
        guard let arguments = value as? [String: Any],
              Set(arguments.keys).isSubset(of: ["mode", "reason", "secrets", "domains", "repositories"]),
              ["repositories", "name", "secrets", "network", "review"].contains(arguments["mode"] as? String ?? "") else { return false }
        if let reason = arguments["reason"], !validText(reason, max: 512) { return false }
        for (key, maximum) in [("domains", 253), ("repositories", 512)] {
            if let value = arguments[key] {
                guard let entries = value as? [String], entries.allSatisfy({ validText($0, max: maximum) }), key != "repositories" || entries.count <= 10 else { return false }
            }
        }
        if let value = arguments["secrets"] {
            guard let secrets = value as? [[String: Any]], secrets.allSatisfy({
                Set($0.keys).isSubset(of: ["name", "target"]) && validText($0["name"], max: 128)
            }) else { return false }
        }
        return true
    }
    static func elicitation(_ params: [String: Any]) -> PendingRequestKind? {
        let mode = params["mode"] as? String
        if mode == "openai/userVerification" { return .approval }
        let metadata = params["_meta"] as? [String: Any] ?? [:]
        if metadata["codex_approval_kind"] as? String == "browser_auth" {
            guard mode == "form", let challenge = metadata["browser_auth_challenge_id"] as? String,
                  challenge.range(of: "^[A-Za-z0-9_-]{32,128}$", options: .regularExpression) != nil,
                  let fields = metadata["fields"] as? [[String: Any]], fields.count <= 6,
                  metadata["reason"] is String, let origin = metadata["origin"] as? String,
                  let url = URL(string: origin), ["http", "https"].contains(url.scheme ?? "") else { return nil }
            // The native browser-auth schema is independent; uncertain special forms stay unavailable.
            return .unavailable
        }
        if ["openai/form", "openaiForm"].contains(mode ?? "") { return .form }
        if mode == "url" {
            guard let value = params["url"] as? String, let url = URL(string: value), url.scheme == "https", url.user == nil, url.password == nil else { return nil }
            return params["serverName"] as? String == "codex_apps" ? .unavailable : .approval
        }
        guard mode == "form" else { return nil }
        if metadata["codex_approval_kind"] as? String == "browser_email_otp" {
            guard ["node_repl", "cua_repl"].contains(params["serverName"] as? String ?? ""),
                  metadata["connector_id"] as? String == "browser-use",
                  let challenge = metadata["browser_auth_challenge_id"] as? String,
                  challenge.range(of: "^[A-Za-z0-9_-]{32,128}$", options: .regularExpression) != nil,
                  let handle = metadata["otp_handle"] as? String,
                  handle.range(of: "^[A-Za-z0-9_-]{21}[AQgw]$", options: .regularExpression) != nil,
                  let origin = metadata["origin"] as? String, metadata["frame_origin"] as? String == origin,
                  let url = URL(string: origin), url.scheme == "https", url.user == nil, url.password == nil,
                  let schema = params["requestedSchema"] as? [String: Any],
                  let properties = schema["properties"] as? [String: Any], properties.count == 1,
                  (properties["approved"] as? [String: Any])?["type"] as? String == "boolean",
                  schema["required"] as? [String] == ["approved"] else { return nil }
            return .unavailable
        }
        if metadata["codex_approval_kind"] as? String == "mcp_tool_call" { return .approval }
        if metadata["codex_approval_kind"] as? String == "tool_suggestion" {
            let optionalStrings = ["install_url", "suggestion_id"].allSatisfy { metadata[$0] == nil || metadata[$0] is String }
            let remoteID = metadata["remote_plugin_id"] == nil || validText(metadata["remote_plugin_id"], max: Int.max)
            let valid = ["install", "enable"].contains(metadata["suggest_type"] as? String ?? "")
                && metadata["suggest_reason"] is String && metadata["tool_id"] is String && metadata["tool_name"] is String
                && (metadata["persist"] == nil || metadata["persist"] as? String == "always")
                && (metadata["tool_type"] as? String == "connector" && metadata["install_url"] is String
                    || metadata["tool_type"] as? String == "plugin" && optionalStrings && remoteID)
            if valid { return .approval }
        }
        let schema = params["requestedSchema"] as? [String: Any] ?? [:]
        if ["browser", "browser-use"].contains(params["serverName"] as? String ?? ""),
           (schema["properties"] as? [String: Any])?.isEmpty == true,
           validPersist(metadata["persist"]), validText(metadata["origin"], max: Int.max) { return .approval }
        if let value = metadata["openai/elicitation"] {
            guard let data = value as? [String: Any], data.keys.contains("requestedSchema") else { return .approval }
        }
        return .form
    }
    static func nativeAttention(_ ordered: [[String: Any]], requests: [[String: Any]]) -> PendingRequestKind? {
        let pending = requests.reversed().filter { $0["completed"] as? Bool != true }
        for turn in ordered.reversed() {
            guard let id = turn["turnId"] as? String else { continue }
            let relevant = pending.filter { ($0["params"] as? [String: Any])?["turnId"] as? String == id }
            if relevant.contains(where: {
                if $0["method"] as? String == "item/tool/requestUserInput" { return true }
                guard $0["method"] as? String == "item/tool/call", let params = $0["params"] as? [String: Any] else { return false }
                return params["tool"] as? String == "request_environment_input" && validEnvironmentInput(params["arguments"])
            }) { return .input }
            if relevant.contains(where: { $0["method"] as? String == "item/tool/requestOptionPicker" }) { return .other }
            let items = turn["items"] as? [[String: Any]] ?? []
            if items.contains(where: { $0["type"] as? String == "userInputResponse" && $0["completed"] as? Bool == false && $0.keys.contains("requestId") }) { return .input }
            if relevant.contains(where: { request in
                switch request["method"] as? String {
                case "item/commandExecution/requestApproval": return true
                case "item/fileChange/requestApproval":
                    let itemID = (request["params"] as? [String: Any])?["itemId"] as? String
                    let changes = (items.first { $0["type"] as? String == "fileChange" && $0["id"] as? String == itemID })?["changes"] as? [[String: Any]] ?? []
                    return changes.contains { change in (change["kind"] as? [String: Any])?["type"] as? String != "delete" }
                default: return false
                }
            }) { return .approval }
            if relevant.contains(where: { $0["method"] as? String == "item/permissions/requestApproval" }) { return .approval }
            for request in relevant where request["method"] as? String == "mcpServer/elicitation/request" {
                if let kind = elicitation(request["params"] as? [String: Any] ?? [:]) { return kind }
            }
        }
        for request in pending where request["method"] as? String == "mcpServer/elicitation/request" {
            let params = request["params"] as? [String: Any] ?? [:]
            if (params["turnId"] as? String ?? "").isEmpty, let kind = elicitation(params) { return kind }
        }
        return nil
    }
    static func unansweredQuestions(_ ordered: [[String: Any]]) -> Bool {
        guard let turn = ordered.reversed().first(where: { $0["status"] as? String == "inProgress" }), turn["turnId"] is String else { return false }
        var questionIDs = Set<String>(), answered = Set<String>()
        for item in turn["items"] as? [[String: Any]] ?? [] {
            switch item["type"] as? String {
            case "agentMessage":
                guard let id = item["id"] as? String else { continue }
                for index in (item["questions"] as? [Any] ?? []).indices {
                    if let bytes = try? JSONSerialization.data(withJSONObject: ["request_user_input_async", id, index]), let id = String(data: bytes, encoding: .utf8) { questionIDs.insert(id) }
                }
            case "userMessage", "steeringUserMessage":
                let steering = item["type"] as? String == "steeringUserMessage"
                if steering && item["status"] as? String != "accepted" { continue }
                let parts = item[steering ? "input" : "content"] as? [[String: Any]] ?? []
                let text = parts.filter { $0["type"] as? String == "text" }.compactMap { $0["text"] as? String }.joined(separator: "\n")
                answered.formUnion(replyIDs(text))
            default: break
            }
        }
        return !questionIDs.subtracting(answered).isEmpty
    }
    static func attentionNeedsSnapshot(_ patches: [[String: Any]], pending: Bool) -> Bool {
        patches.contains { patch in
            guard let path = patch["path"] as? [Any], let first = path.first as? String else { return false }
            let keys = path.map { String(describing: $0) }
            if first == "requests" || first == "resumeState" { return true }
            guard first == "turnHistory" || first == "turns" else { return false }
            if first == "turnHistory" && (keys.count <= 4 || keys.contains("islands")) || first == "turns" && keys.count <= 2 { return true }
            if keys.contains("questions") { return true }
            let value = patch["value"]
            guard let index = keys.firstIndex(of: "items") else {
                return pending && keys.last == "status"
            }
            let depth = keys.count - index
            if depth <= 2 {
                if patch["op"] as? String == "remove" || pending && patch["op"] as? String != "add" || depth == 1 { return true }
                let items = (value as? [[String: Any]]) ?? (value as? [String: Any]).map { [$0] } ?? []
                return items.contains { item in
                    ["error", "userInputResponse"].contains(item["type"] as? String ?? "")
                        || !(item["questions"] as? [Any] ?? []).isEmpty
                        || ["userMessage", "steeringUserMessage"].contains(item["type"] as? String ?? "")
                }
            }
            if ["willRetry", "errorInfo", "message", "requestId", "completed"].contains(keys.last ?? "") { return true }
            if keys.last == "type" { return true }
            if depth >= 3, ["content", "input"].contains(keys[index + 2]) {
                if depth <= 4 { return true }
                if patch["op"] as? String == "remove" { return true }
            }
            if keys.last == "status" && value as? String == "accepted" { return true }
            if depth == 5, ["content", "input"].contains(keys[index + 2]), keys.last == "text", let text = value as? String { return !replyIDs(text).isEmpty }
            return false
        }
    }
    mutating func suppressReady() { readyTurn = nil }
    mutating func applyReadState(_ hasUnreadTurn: Bool) {
        let acknowledge = phase == .ready || control?.status != "inProgress"
        let wasRead = readTurn == currentKey
        let becameUnread = hasUnreadTurn && (!unread || wasRead)
        unread = hasUnreadTurn
        if !unread {
            readyTurn = nil
            if acknowledge { readTurn = currentKey }
        } else {
            readTurn = nil
            if becameUnread { readyTurn = currentKey }
        }
    }
    mutating func forgetReadAcknowledgement() {
        guard let readTurn else { return }
        if readTurn == currentKey { unread = false }
        self.readTurn = nil
    }
    mutating func refreshReady(previousKey: String?, previousStatus: String?, initial: Bool, becameUnread: Bool) {
        if previousStatus == "inProgress", latest?.status != "inProgress", readTurn == currentKey { readTurn = nil }
        guard unread, readTurn != currentKey else { readyTurn = nil; return }
        if initial || previousKey != currentKey || becameUnread || previousStatus == "inProgress" && latest?.status != "inProgress" { readyTurn = currentKey }
    }
    mutating func patches(_ patches: [[String: Any]], base: Int, revision next: Int) throws {
        guard base == revision, next > base else { throw MonitorError.incompatibleState }
        let previousKey = currentKey, previousStatus = latest?.status, wasUnread = unread
        for patch in patches {
            guard let path = patch["path"] as? [Any], !path.isEmpty,
                  let op = patch["op"] as? String, ["add", "replace", "remove"].contains(op) else { throw MonitorError.incompatibleState }
            let value: Any? = op == "remove" ? nil : patch["value"]
            let keys = path.map { String(describing: $0) }
            switch keys[0] {
            case "threadRuntimeStatus":
                if keys.count == 1 {
                    guard let next = value as? [String: Any], next["type"] is String else { throw MonitorError.incompatibleState }
                    runtime = next
                } else if keys.count == 2 { runtime[keys[1]] = value }
                else { throw MonitorError.incompatibleState }
            case "title": title = value as? String ?? "Codex conversation"
            case "hasUnreadTurn":
                guard let flag = value as? Bool else { throw MonitorError.incompatibleState }
                unread = flag
                if !flag { readTurn = nil }
            case "turnHistory":
                guard keys.count >= 5, Array(keys.prefix(3)) == ["turnHistory", "history", "entitiesByKey"], turns[keys[3]] != nil else { throw MonitorError.incompatibleState }
                if keys.count == 5 { turns[keys[3]]?.update(keys[4], value: value) }
            case "turns":
                guard keys.count >= 3, turns["legacy:\(keys[1])"] != nil else { throw MonitorError.incompatibleState }
                if keys.count == 3 { turns["legacy:\(keys[1])"]?.update(keys[2], value: value) }
            default: break // Message bodies, commands, and token deltas are discarded.
            }
        }
        revision = next
        refreshReady(previousKey: previousKey, previousStatus: previousStatus, initial: false, becameUnread: !wasUnread && unread)
    }
}
