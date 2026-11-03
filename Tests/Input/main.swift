import Foundation
func check(_ value: Bool, _ message: String) { if !value { fatalError(message) } }
func question(_ id: String = "q", count: Int = 1) -> [String: Any] { ["type": "agentMessage", "id": id, "questions": Array(repeating: ["title": "Placeholder", "options": []], count: count)] }
func qid(_ index: Int = 0, id: String = "q") -> String { "[\"request_user_input_async\",\"\(id)\",\(index)]" }
func reply(_ ids: [String], steering: String? = nil) -> [String: Any] {
    let payload = ids.map { ["questionItemId": $0, "question": "Placeholder", "answer": "Placeholder"] }
    let text = "<send_user_message_question_reply>\n" + String(data: try! JSONSerialization.data(withJSONObject: payload), encoding: .utf8)! + "\n</send_user_message_question_reply>"
    let part: [[String: Any]] = [["type": "text", "text": text]]
    if let steering { return ["type": "steeringUserMessage", "status": steering, "input": part] }
    return ["type": "userMessage", "content": part]
}
func turn(_ id: String, at: Int, status: String = "inProgress", items: [[String: Any]] = []) -> [String: Any] { ["turnId": id, "status": status, "turnStartedAtMs": at, "items": items] }
func state(_ turns: [[String: Any]], requests: [[String: Any]] = []) -> [String: Any] {
    ["id": "chat", "title": "Fixture", "originator": "codex_work_desktop", "source": "vscode", "threadSource": "user", "threadRuntimeStatus": ["type": "active", "activeFlags": []], "requests": requests, "resumeState": "resumed", "hasUnreadTurn": false,
     "turnHistory": ["kind": "canonical", "history": ["entitiesByKey": Dictionary(uniqueKeysWithValues: turns.enumerated().map { (String($0.offset), $0.element) }), "islands": [["newerBoundary": ["status": "exhausted"], "entries": turns.indices.map { ["value": String($0)] }]]]]]
}
func phase(_ turns: [[String: Any]], requests: [[String: Any]] = []) throws -> SessionPhase { try Conversation.snapshot(state(turns, requests: requests), revision: 1, previous: nil).phase }
check(try phase([turn("t", at: 1, items: [question()])]) == .waiting, "Async unanswered input despite empty runtime flags")
check(try phase([turn("t", at: 1, items: [question(), reply([qid()])])]) == .working, "Accepted user reply clears input")
check(try phase([turn("t", at: 1, items: [question(), reply([qid()], steering: "pending")])]) == .waiting, "Pending steering does not clear input")
check(try phase([turn("t", at: 1, items: [question(), reply([qid()], steering: "accepted")])]) == .working, "Accepted steering clears input")
check(try phase([turn("t", at: 1, items: [question(count: 2), reply([qid()])])]) == .waiting, "One answer cannot clear second question")
check(try phase([turn("t", at: 1, items: [question(count: 2), reply([qid(), qid(1)])])]) == .working, "All answers clear input")
check(try phase([turn("older", at: 1, items: [question()]), turn("newer", at: 2, status: "completed")]) == .waiting, "Older in-progress turn can still supply input")
check(try phase([turn("older", at: 1, items: [question()]), turn("newer", at: 2)]) == .working, "Only newest in-progress turn is consulted")
check(try phase([turn("t", at: 1, status: "completed", items: [question()])]) == .working, "Completed turn questions do not imply input")
let native: [String: Any] = ["method": "item/tool/requestUserInput", "params": ["turnId": "t"]]
check(try phase([turn("t", at: 1)], requests: [native]) == .waiting, "Native input request is recognized")
check(try phase([turn("t", at: 1)]) == .working, "Removed native request clears input")
check(try phase([turn("t", at: 1)], requests: [["method": "item/permissions/requestApproval", "params": ["turnId": "t"]]]) == .approval, "Native permission approval")
let part = (reply([qid()])["content"] as! [[String: Any]])[0]["text"] as! String
check(Conversation.replyIDs("Context\n## My request:\n" + part) == [qid()], "Last request suffix")
check(Conversation.replyIDs("[from: user]\n" + part) == [qid()], "Sender prefix")
check(Conversation.replyIDs("<codex_multi_user_message><body>" + part + "</body></codex_multi_user_message>") == [qid()], "Sender body wrapper")
check(Conversation.replyIDs("A quotation: " + part).isEmpty, "Quoted reply is not accepted")
check(Conversation.replyIDs("<send_user_message_question_reply>{\"questionItemId\":\"q\"}</send_user_message_question_reply>").isEmpty, "Malformed reply cannot clear input")
func patch(_ path: [Any], _ value: Any, op: String = "replace") -> [[String: Any]] { [["op": op, "path": path, "value": value]] }
let items: [Any] = ["turnHistory", "history", "entitiesByKey", "turn", "items"]
check(Conversation.attentionNeedsSnapshot(patch(items + [0], question()), pending: false), "New question item refreshes")
check(Conversation.attentionNeedsSnapshot(patch(items + [0, "questions"], []), pending: true), "Question replacement refreshes")
check(Conversation.attentionNeedsSnapshot(patch(items + [1], reply([qid()])), pending: true), "Reply item refreshes")
check(Conversation.attentionNeedsSnapshot(patch(items + [1, "status"], "accepted"), pending: true), "Accepted steering refreshes")
check(Conversation.attentionNeedsSnapshot(patch(["requests"], []), pending: true), "Request removal refreshes")
check(Conversation.attentionNeedsSnapshot(patch(items + [0], NSNull(), op: "remove"), pending: true), "Question item removal refreshes")
check(Conversation.attentionNeedsSnapshot(patch(["turnHistory", "history", "entitiesByKey", "turn", "status"], "completed"), pending: true), "Turn completion refreshes pending input")
check(!Conversation.attentionNeedsSnapshot(patch(items + [0, "text"], "Ordinary streamed text"), pending: true), "Streaming text does not fetch full history")
check(!Conversation.attentionNeedsSnapshot(patch(items + [0, "status"], "completed"), pending: true), "Tool completion does not fetch full history")
check(!Conversation.attentionNeedsSnapshot(patch(["latestTokenUsageInfo"], [:]), pending: true), "Token deltas do not fetch full history")
var unresumed = state([turn("t", at: 1)], requests: [native]); unresumed["resumeState"] = "needs_resume"
check(try Conversation.snapshot(unresumed, revision: 1, previous: nil).phase == .working, "Unresumed blocking request uses runtime flags")
check(Conversation.replyIDs("[from: missing bracket\n" + part).isEmpty, "Malformed sender prefix is not a reply")
check(!Conversation.attentionNeedsSnapshot(patch(items + [0, "text"], part), pending: true), "Agent text with a literal reply is not a refresh")
check(!Conversation.attentionNeedsSnapshot(patch(items + [0, "summary", 0], NSNull(), op: "remove"), pending: true), "Nested nonquestion removal is not a refresh")
check(!Conversation.attentionNeedsSnapshot(patch(items + [1, "content", 0, "text"], "Quoted: " + part), pending: true), "Quoted reply content is not a refresh")
check(Conversation.attentionNeedsSnapshot(patch(items + [1, "content", 0, "text"], part), pending: true), "Canonical user reply text refreshes")
var cachedAsyncState = state([turn("t", at: 1, items: [question()])])
cachedAsyncState["resumeState"] = "needs_resume"
cachedAsyncState["requests"] = [["method": "item/commandExecution/requestApproval", "params": ["turnId": "t"]]]
cachedAsyncState["threadRuntimeStatus"] = ["type": "active", "activeFlags": ["waitingOnApproval"]]
var cachedAsync = try Conversation.snapshot(cachedAsyncState, revision: 1, previous: nil)
check(cachedAsync.pendingAttention == .approval && cachedAsync.phase == .approval, "Live unresumed approval takes priority without discarding cached async input")
cachedAsync.runtime["activeFlags"] = [String]()
check(cachedAsync.phase == .waiting, "Cleared runtime approval reveals still-unanswered async input")
check(Conversation.attentionNeedsSnapshot(patch(["resumeState"], "resumed"), pending: true), "Resume change recomputes pending attention")
let placeholder: [String: Any] = ["status": "inProgress", "turnStartedAtMs": 100, "items": []]
check(try phase([turn("valid", at: 1, items: [question()]), placeholder]) == .working, "Newest in-progress placeholder follows native async selector")
check(Conversation.attentionNeedsSnapshot(patch(items, []), pending: true), "Replacing all items clears pending questions")
check(Conversation.attentionNeedsSnapshot(patch(items + [0], ["type": "agentMessage", "id": "q"]), pending: true), "Replacing a question item without questions refreshes")
check(!Conversation.attentionNeedsSnapshot(patch(items + [2], ["type": "commandExecution", "status": "inProgress"], op: "add"), pending: true), "Adding unrelated tool items does not fetch history")
let inputRequest: [String: Any] = ["method": "item/tool/requestUserInput", "params": ["turnId": "t"]]
let commandApproval: [String: Any] = ["method": "item/commandExecution/requestApproval", "params": ["turnId": "t"]]
check(try phase([turn("t", at: 1)], requests: [commandApproval, inputRequest]) == .waiting, "Same-turn direct input precedes approval")
check(try phase([turn("old", at: 100), turn("t", at: 1)], requests: [["method": "item/tool/requestUserInput", "params": ["turnId": "old"]], commandApproval]) == .approval, "Newest ordered relevant request precedes older input")
check(try phase([turn("t", at: 1)], requests: [["method": "item/tool/requestUserInput", "params": ["turnId": "unloaded"]]]) == .working, "Unmatched stale request cannot fabricate input")
check(try phase([turn("t", at: 1, items: [["type": "userInputResponse", "completed": false, "requestId": "r"]])]) == .waiting, "Unfinished native response item supplies input")
check(try phase([turn("t", at: 1, items: [["type": "userInputResponse", "completed": true, "requestId": "r"]])]) == .working, "Completed native response item clears input")
let form: [String: Any] = ["method": "mcpServer/elicitation/request", "params": ["turnId": "t", "mode": "form", "requestedSchema": ["type": "object", "properties": ["name": ["type": "string"]]]]]
check(try phase([turn("t", at: 1)], requests: [form]) == .waiting, "Ordinary form elicitation is input")
let toolForm: [String: Any] = ["method": "mcpServer/elicitation/request", "params": ["turnId": "t", "mode": "form", "_meta": ["codex_approval_kind": "mcp_tool_call"], "requestedSchema": ["type": "object", "properties": [:]]]]
check(try phase([turn("t", at: 1)], requests: [toolForm]) == .approval, "Tool authorization form remains approval")
check(try phase([turn("ordered-last", at: 999, items: [question()]), turn("current", at: 1)]) == .working, "Async questions follow canonical order rather than timestamp")
check(Conversation.attentionNeedsSnapshot(patch(items + [1, "input"], []), pending: true), "Whole accepted reply input-array refreshes")
check(Conversation.attentionNeedsSnapshot(patch(items + [1, "content", 0], ["type": "text", "text": part]), pending: false), "Reply part replacement refreshes")
check(Conversation.attentionNeedsSnapshot(patch(["turnHistory", "history", "islands", 0, "entries", 0, "value"], "other"), pending: false), "Canonical order mutation refreshes")
check(Conversation.attentionNeedsSnapshot(patch(items + [0], ["type": "error", "willRetry": false, "message": "Fixture", "errorInfo": NSNull()], op: "add"), pending: false), "New terminal error item refreshes")
check(Conversation.attentionNeedsSnapshot(patch(items + [0, "willRetry"], false), pending: false), "Error retry-state mutation refreshes")
check(Conversation.attentionNeedsSnapshot(patch(items + [0, "completed"], true), pending: true), "Native response completion refreshes")
func elicitationPhase(_ parameters: [String: Any]) throws -> SessionPhase {
    var parameters = parameters; parameters["turnId"] = "t"
    return try phase([turn("t", at: 1)], requests: [["method": "mcpServer/elicitation/request", "params": parameters]])
}
check(try elicitationPhase(["mode": "form", "_meta": ["codex_approval_kind": "browser_auth"]]) == .working, "Malformed browser-auth metadata does not invent approval")
check(try elicitationPhase(["mode": "openai/form", "_meta": ["codex_approval_kind": "browser_auth"]]) == .working, "Browser-auth validation precedes explicit form modes")
check(try elicitationPhase(["mode": "form", "_meta": ["codex_approval_kind": "tool_suggestion"]]) == .waiting, "Malformed tool suggestion falls back to ordinary form input")
let suggestion: [String: Any] = ["codex_approval_kind": "tool_suggestion", "suggest_type": "install", "suggest_reason": "Fixture", "tool_id": "tool", "tool_name": "Fixture", "tool_type": "plugin"]
check(try elicitationPhase(["mode": "form", "_meta": suggestion]) == .approval, "Valid tool suggestion uses approval")
var invalidSuggestion = suggestion; invalidSuggestion["persist"] = "session"
check(try elicitationPhase(["mode": "form", "_meta": invalidSuggestion]) == .waiting, "Tool suggestion persist must be always when supplied")
let browserForm: [String: Any] = ["mode": "form", "serverName": "browser", "requestedSchema": ["properties": [:]]]
for invalid in ["invalid" as Any, [] as Any, ["always", "session", "always"] as Any] {
    var parameters = browserForm; parameters["_meta"] = ["persist": invalid, "origin": "https://example.com"]
    check(try elicitationPhase(parameters) == .waiting, "Invalid browser persist cannot invent approval")
}
var browser = browserForm; browser["_meta"] = ["persist": "session", "origin": "  "]
check(try elicitationPhase(browser) == .waiting, "Empty browser origin cannot invent approval")
browser["_meta"] = ["persist": ["session", "always"], "origin": "https://example.com"]
check(try elicitationPhase(browser) == .approval, "Valid browser persist/origin uses approval")
let possibleAuth: [String: Any] = ["codex_approval_kind": "browser_auth", "browser_auth_challenge_id": String(repeating: "a", count: 32), "fields": [], "reason": "Fixture", "origin": "https://example.com"]
check(try elicitationPhase(["mode": "form", "_meta": possibleAuth]) == .unknown, "Unsupported special auth schema is unavailable rather than guessed approval")
func environmentPhase(_ arguments: [String: Any]) throws -> SessionPhase {
    try phase([turn("t", at: 1)], requests: [["method": "item/tool/call", "params": ["turnId": "t", "tool": "request_environment_input", "arguments": arguments]]])
}
check(try environmentPhase([:]) == .working, "Empty environment arguments do not invent input")
check(try environmentPhase(["mode": "unknown"]) == .working, "Unknown environment mode does not invent input")
check(try environmentPhase(["mode": "review", "reason": "  "]) == .working, "Invalid optional environment reason is rejected")
check(try environmentPhase(["mode": "review", "repositories": Array(repeating: "repo", count: 11)]) == .working, "Environment repository limit is validated")
check(try environmentPhase(["mode": "secrets", "secrets": [["name": "", "target": [:]]]]) == .working, "Empty environment secret name is rejected")
check(try environmentPhase(["mode": "review", "unexpected": true]) == .working, "Strict environment argument shape rejects extra keys")
check(try environmentPhase(["mode": "review", "domains": ["example.com"], "repositories": ["repo"], "secrets": [["name": "TOKEN", "target": [:]]]]) == .waiting, "Valid environment input is recognized")
print("Input metadata and semantic refresh checks passed")
