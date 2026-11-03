import Foundation

func check(_ condition: Bool, _ name: String) { if !condition { fatalError(name) } }
func history(_ entries: [(String, [String: Any])]) -> [String: Any] {
    ["kind": "canonical", "history": ["entitiesByKey": Dictionary(uniqueKeysWithValues: entries),
        "islands": [["newerBoundary": ["status": "exhausted"], "entries": entries.map { ["value": $0.0] }]]]]
}
func state(_ status: String, runtime: String = "idle", unread: Bool = false, turn: String = "turn-1", items: [[String: Any]] = []) -> [String: Any] {
    ["id": "chat", "title": "Test", "originator": "codex_work_desktop", "source": "vscode", "threadSource": "user",
     "threadRuntimeStatus": ["type": runtime, "activeFlags": []], "hasUnreadTurn": unread, "resumeState": "resumed", "requests": [],
     "turnHistory": history([("entity", ["turnId": turn, "status": status, "items": items])])]
}
func phase(_ value: [String: Any]) throws -> SessionPhase { try Conversation.snapshot(value, revision: 1, previous: nil).phase }
let terminalError: [String: Any] = ["type": "error", "willRetry": false, "message": "Fixture", "errorInfo": NSNull()]
var frames = FrameBuffer()
let frame = try FrameBuffer.encode(["type": "test"])
check(try frames.append(Data(frame.prefix(2))).isEmpty, "partial header")
check(try frames.append(Data(frame.dropFirst(2).prefix(4))).isEmpty, "partial body")
check(try frames.append(Data(frame.dropFirst(6)) + frame).count == 2, "fragmented and coalesced frames")
do { var invalid = FrameBuffer(); _ = try invalid.append(Data([255, 255, 255, 127])); fatalError("oversized frame accepted") }
catch MonitorError.invalidFrame {}
check(Conversation.desktop(state("completed")), "desktop root accepted")
for (field, value) in [("source", ["subAgent": "child"] as Any), ("originator", "codex_cli" as Any), ("sideConversation", true as Any), ("parentThreadId", "parent" as Any)] {
    var child = state("completed"); child[field] = value
    check(!Conversation.desktop(child), "nonroot excluded: " + field)
}
var active = try Conversation.snapshot(state("inProgress", runtime: "active", unread: true), revision: 1, previous: nil)
check(active.phase == .working, "unread during work is not ready")
active.runtime["activeFlags"] = ["waitingOnUserInput"]
check(active.phase == .working, "resumed runtime flags do not fabricate input")
active.resumeState = "needs_resume"
check(active.phase == .waiting, "unresumed active input flag recovers input")
active.runtime["activeFlags"] = ["waitingOnApproval"]
check(active.phase == .working, "unresumed approval flag needs an actionable request")
active.hasApprovalRequest = true
check(active.phase == .approval, "unresumed actionable approval")
var completed = try Conversation.snapshot(state("completed", unread: true), revision: 2, previous: active)
check(completed.phase == .ready, "unread idle answer is ready without timestamp")
completed.suppressReady()
completed = try Conversation.snapshot(state("completed", unread: true), revision: 3, previous: completed)
check(completed.phase == .idle, "manual clear survives duplicate snapshot")
let newActive = try Conversation.snapshot(state("inProgress", runtime: "active", turn: "turn-2"), revision: 4, previous: completed)
let newCompleted = try Conversation.snapshot(state("completed", unread: true, turn: "turn-2"), revision: 5, previous: newActive)
check(newCompleted.phase == .ready, "next turn gets its own readiness")
check(try phase(state("inProgress")) == .working, "control turn can be working under idle runtime")
check(try phase(state("completed", unread: false)) == .idle, "read answers do not light indicator")
check(try phase(state("interrupted", unread: true)) == .ready, "native unread is independent of interrupted status")
check(try phase(state("failed")) == .idle, "failed status alone is not native error signal")
check(try phase(state("completed", unread: true, items: [terminalError])) == .failed, "terminal error item overrides unread")
check(try phase(state("inProgress", items: [terminalError])) == .failed, "terminal error does not require failed turn status")
check(try phase(state("completed", runtime: "active", items: [terminalError])) == .working, "active runtime precedes terminal error item")
check(try phase(state("completed", runtime: "systemError", unread: true)) == .failed, "runtime system error precedes ready")
var retry = terminalError; retry["willRetry"] = true
check(try phase(state("completed", items: [retry])) == .idle, "retrying errors do not imply failure")
var unresumed = state("inProgress", unread: true, items: [terminalError]); unresumed["resumeState"] = "needs_resume"
check(try phase(unresumed) == .ready, "needs_resume suppresses stale control and item error")
check(try phase(state("unexpected")) == .unknown, "unknown turn status fails closed")
check(try phase(state("completed", runtime: "notLoaded", unread: true)) == .unknown, "missing summary fallback never guesses idle")
var ordered = state("completed")
ordered["turnHistory"] = history([
    ("z", ["turnId": "older", "status": "completed", "turnStartedAtMs": 900, "items": []]),
    ("a", ["turnId": "current", "status": "inProgress", "items": []])])
let orderedConversation = try Conversation.snapshot(ordered, revision: 1, previous: nil)
check(orderedConversation.latest?.id == "current" && orderedConversation.phase == .working, "canonical order wins over timestamps/map keys")
ordered["turnHistory"] = history([
    ("control", ["turnId": "current", "status": "inProgress", "items": []]),
    ("placeholder", ["status": "completed", "items": []])])
let placeholder = try Conversation.snapshot(ordered, revision: 1, previous: nil)
check(placeholder.latest?.id == nil && placeholder.control?.id == "current" && placeholder.phase == .working, "turnless completed row does not replace control turn")
var malformed = state("completed"); malformed["turnHistory"] = ["kind": "canonical", "history": ["entitiesByKey": [:]]]
do { _ = try phase(malformed); fatalError("missing canonical order accepted") } catch MonitorError.incompatibleState {}
var legacy = state("completed"); legacy.removeValue(forKey: "turnHistory"); legacy["turns"] = [["turnId": "legacy", "status": "inProgress"]]
check(try phase(legacy) == .working, "legacy array order supported")
var patched = try Conversation.snapshot(state("inProgress", runtime: "active"), revision: 1, previous: nil)
try patched.patches([
    ["op": "replace", "path": ["turnHistory", "history", "entitiesByKey", "entity", "status"], "value": "completed"],
    ["op": "replace", "path": ["threadRuntimeStatus"], "value": ["type": "idle"]],
    ["op": "replace", "path": ["hasUnreadTurn"], "value": true]
], base: 1, revision: 2)
check(patched.phase == .ready, "ordered metadata completion patch")
do { try patched.patches([], base: 1, revision: 3); fatalError("revision gap accepted") } catch MonitorError.incompatibleState {}
let priorTurn = patched.latest?.id
try patched.patches([["op": "replace", "path": ["turnHistory", "history", "entitiesByKey", "entity", "items", 0, "text"], "value": "private text"]], base: 2, revision: 3)
check(patched.latest?.id == priorTurn && patched.turns.count == 1, "message bodies discarded")
var readSynced = try Conversation.snapshot(state("completed", unread: true), revision: 1, previous: nil)
readSynced.applyReadState(false)
check(readSynced.phase == .idle && readSynced.readyTurn == nil, "Codex read clears immediately")
readSynced = try Conversation.snapshot(state("completed", unread: true), revision: 2, previous: readSynced)
check(readSynced.phase == .idle, "stale same-answer unread snapshot does not resurrect read answer")
readSynced.applyReadState(true)
check(readSynced.phase == .ready, "explicit mark unread can restore acknowledged answer")
readSynced.suppressReady(); readSynced.applyReadState(true)
check(readSynced.phase == .idle, "duplicate unread event preserves local Clear")
let readSnapshot = try Conversation.snapshot(state("completed", unread: false), revision: 3, previous: readSynced)
check(readSnapshot.phase == .idle, "canonical false clears old global true")
var lateUnread = try Conversation.snapshot(state("completed"), revision: 1, previous: nil)
try lateUnread.patches([["op": "replace", "path": ["hasUnreadTurn"], "value": true]], base: 1, revision: 2)
check(lateUnread.phase == .ready, "late unread can arm already idle answer")
var readDuringWork = try Conversation.snapshot(state("inProgress", runtime: "active", unread: true), revision: 1, previous: nil)
readDuringWork.applyReadState(false)
let afterWork = try Conversation.snapshot(state("completed", unread: true), revision: 2, previous: readDuringWork)
check(afterWork.phase == .ready, "reading during work does not acknowledge its future answer")
var acknowledgement = try Conversation.snapshot(state("completed", unread: true), revision: 1, previous: nil)
acknowledgement.applyReadState(false)
acknowledgement = try Conversation.snapshot(state("completed", unread: true), revision: 2, previous: acknowledgement)
acknowledgement.forgetReadAcknowledgement()
check(try Conversation.snapshot(state("completed", unread: true), revision: 1, previous: acknowledgement).phase == .ready, "reconnect canonical unread rebases old acknowledgement")
var newerClear = try Conversation.snapshot(state("completed", unread: true), revision: 1, previous: nil)
newerClear.applyReadState(false)
newerClear = try Conversation.snapshot(state("completed", unread: true, turn: "turn-2"), revision: 2, previous: newerClear)
newerClear.suppressReady(); newerClear.forgetReadAcknowledgement()
check(try Conversation.snapshot(state("completed", unread: true, turn: "turn-2"), revision: 1, previous: newerClear).phase == .idle, "rebasing older acknowledgement preserves newer manual Clear")
var emptyNewest = state("completed", items: [terminalError])
var newestHistory = emptyNewest["turnHistory"] as! [String: Any]
var newestCanonical = newestHistory["history"] as! [String: Any]
var newestIslands = newestCanonical["islands"] as! [[String: Any]]
newestIslands.append(["newerBoundary": ["status": "exhausted"], "entries": []])
newestCanonical["islands"] = newestIslands; newestHistory["history"] = newestCanonical; emptyNewest["turnHistory"] = newestHistory
check(try Conversation.snapshot(emptyNewest, revision: 1, previous: nil).latest == nil, "Empty newest exhausted island has no latest turn")
check(try phase(emptyNewest) == .idle, "Empty newest island does not reuse older error item")
var resumedRead = try Conversation.snapshot(unresumed, revision: 1, previous: nil)
resumedRead.applyReadState(false)
resumedRead = try Conversation.snapshot(unresumed, revision: 2, previous: resumedRead)
check(resumedRead.phase == .idle, "Reading an unresumed ready answer suppresses same-answer stale unread")
let finishedAfterResume = try Conversation.snapshot(state("completed", unread: true), revision: 3, previous: resumedRead)
check(finishedAfterResume.phase == .ready, "A later completion of resumed control turn can rearm")
var missingRead = state("completed"); missingRead.removeValue(forKey: "hasUnreadTurn")
do { _ = try phase(missingRead); fatalError("missing native unread evidence accepted") } catch MonitorError.incompatibleState {}
var missedUnreadSnapshot = try Conversation.snapshot(state("completed", unread: true), revision: 1, previous: nil)
missedUnreadSnapshot.applyReadState(false)
missedUnreadSnapshot = try Conversation.snapshot(state("completed", unread: true), revision: 2, previous: missedUnreadSnapshot)
check(missedUnreadSnapshot.phase == .idle, "Read acknowledgement protects against an unconfirmed stale snapshot")
missedUnreadSnapshot = try Conversation.snapshot(state("completed", unread: false), revision: 3, previous: missedUnreadSnapshot)
missedUnreadSnapshot = try Conversation.snapshot(state("completed", unread: true), revision: 4, previous: missedUnreadSnapshot)
check(missedUnreadSnapshot.phase == .ready, "Canonical mark-unread after confirmed read recovers without a global event")
var missedUnreadPatch = try Conversation.snapshot(state("completed", unread: true), revision: 1, previous: nil)
missedUnreadPatch.applyReadState(false)
try missedUnreadPatch.patches([["op": "replace", "path": ["hasUnreadTurn"], "value": false]], base: 1, revision: 2)
try missedUnreadPatch.patches([["op": "replace", "path": ["hasUnreadTurn"], "value": true]], base: 2, revision: 3)
check(missedUnreadPatch.phase == .ready, "Read confirmation patch allows a later canonical unread patch to recover")
print("Core self-checks passed")
