# Wink: signal and design contract

Status: initial public release 0.1.6 prepared. Earlier version numbers were local prototypes, not published releases.

## Purpose

Show the state of existing local Codex desktop chats in a small macOS menu-bar app. The expected workload is one Mac user with several desktop chats. Observation must remain read-only; missing evidence must never be presented as a completed or idle chat.

## Scope and invariants

- Native AppKit application with system frameworks only. No helper service, package dependencies, account, API key, or internet connection.
- Track local desktop-owned root chats. Reject CLI sessions, internal subagents, ephemeral chats and side conversations.
- Never send prompts, answer approvals, resume/interrupt/archive chats, mark them read, or alter Codex settings/hooks.
- Full native snapshots are transient. Retain titles and status metadata, discard message/command content, and save no conversation or status history.
- Keep incomplete coverage visible. Isolate a failed chat when possible; malformed transport invalidates the connection.

## Components

| Component | Responsibility |
| --- | --- |
| `Sources/Core.swift` | Bounded framing, desktop provenance, canonical turn order, status/input/read reducers. |
| `Sources/DesktopMonitor.swift` | Local socket, discovery, following, file hints, reconciliation and per-chat recovery. |
| `Sources/main.swift` | Menu-bar counts, native menus, accessibility, colors and working-ring animation. |
| `Tests/` | State assertions, offscreen UI checks and isolated protocol fixtures. |
| `build.sh`, `scripts/` | Native build, complete validation and ZIP/checksum packaging. |

The adapter was verified with desktop build **26.1027.61615**. It uses private IPC stream format 11 and read-state format 3. No upstream application code or extracted resources are included in this repository.

## Signals

Canonical turn order is `turnHistory.history.islands[].entries[].value`. Latest and control turns are different selectors. Timestamps and silence are not status authority.

| Phase | Available-root rule |
| --- | --- |
| Needs input | Relevant native input/form or unfinished input response, with native resume/runtime precedence; otherwise unanswered questions in the last ordered in-progress turn, minus accepted reply references. |
| Needs approval | Relevant native permission/command/file/tool approval after input priority. Complex unsupported authentication is unavailable. |
| Failed | Runtime system error, or a latest nonretrying error item when runtime is not active and the chat is resumed. Failed turn status alone is insufficient. |
| Working | Active runtime, or a resumed control turn in progress with no higher input/approval/error state. |
| Answer ready | Idle native classification plus unread, except an answer dismissed locally. No completed-turn/timestamp heuristic. |
| Unavailable | Missing/malformed evidence, unsupported metadata, an unresponsive owner or revision re-verification. |

## Active decisions

- **D-001 — Native status item.** One executable, no normal window or Dock icon. Login registration is opt-in (D-012).
- **D-002/D-003 — Observe the existing desktop connection.** A separate app-server instance does not establish desktop runtime state. Connect to `CODEX_HOME/ipc/ipc.sock` (default `~/.codex`) using read-only owner discovery and following. Lock filenames are discovery hints, never proof of status.
- **D-004/D-005 — Match native status semantics.** Use default adaptive colors and independent shapes/text. Derive input from native requests and unanswered questions even when runtime flags are empty. Structural question/reply/error changes resnapshot; ordinary streamed text does not.
- **D-006/D-007 — Bound missing-event recovery.** Global read notifications may be omitted; final patches may fail without a retry. Reconcile non-idle chats every five seconds and idle chats every thirty seconds. Retry owner discovery and prune vanished, unowned, unvalidated candidates. Release idle subscriptions after ten seconds.
- **D-007 — Per-chat freshness.** A non-postponable ten-second snapshot deadline makes that chat unavailable and rediscovers ownership. Equal snapshot revisions are valid; lower revisions require explicit re-verification. Archive exclusions reset on reconnect and expire for revalidation.
- **D-007 — Per-answer acknowledgement.** A read event suppresses delayed stale data briefly while canonical confirmation is pending. If confirmation never arrives, the existing reconciliation timer expires the acknowledgement after a five-second grace and requests current owner state; timer alignment can make recovery take about ten seconds. Canonical read=false retires that acknowledgement so later native unread=true can rearm even if its event is missing. Manual dismissal stays local and survives duplicate snapshots. Observation loss rebases native acknowledgement without undoing a newer manual dismissal.
- **D-008 — Lightweight rotation.** Ten fine marks fill a twelve-point ring. A centered layer rotates through -2π every 1.8 seconds. Counters remain still; count changes do not restart the animation. Stop when no chat is working, and honor Reduce Motion. Rendering is compositor-driven, without an application frame timer.
- **D-009 — Wink branding.** Public app/executable/menu labels use Wink. The existing `local.codexdot` bundle identifier and internal protocol labels remain for upgrade compatibility.
- **D-011 — App icon.** User selected concept A: a rose open-eye dot and closed-eye curve on the charcoal tile, matching obim’s palette and minimal geometry. Keep the approved SVG and transparent 1024-pixel PNG master, generate the standard 16–1024 pixel ICNS representations, and copy the icon before signing. Packaging validates the icon reference and bytes. The light SVG remains source artwork; the bundled default is the approved dark tile.
- **D-010 — Release boundaries.** Generated app bundles and archives stay under ignored `build/` and `dist/`. CI runs isolated checks and uploads an artifact; it does not publish a release. The initial public changelog is a baseline, not a reconstructed public release history.
- **D-012 — Launch at Login (2026-11-03, implemented).** User requested an optional startup setting. Use macOS 13+ `SMAppService.mainApp`, with no helper, launch-agent file or saved preference. The menu reads the system status whenever rebuilt; enabled is checked, approval pending is mixed with an explicit label and Login Settings action. Toggling enabled or pending off unregisters it; toggling off on registers it. Report errors without an optimistic checkmark. Never register during app launch. Validation: offscreen status, per-state registration/removal and error-propagation checks pass without changing login settings. An isolated, ad-hoc signed temporary app also completed actual registration (`notFound → enabled`) and removal (`enabled → notRegistered`), then was deleted. No real Wink startup setting was changed. A real logout/login cycle remains a manual check.

- **D-013 — Lightweight recovery optimization (2026-11-04, implemented).** User requested optimization after measured connection (17 ms), five-chat state availability (93 ms), initial catalog completion (10.02 s), missed-event recovery (~2.5 s), idle missed-start recovery (15.69 s), and reconnect (5.20 s). The installed router fixes per-client negative discovery at ten seconds; preserve incomplete reporting until it answers. Keep active/idle reconciliation, snapshot deadline, read-acknowledgement grace and idle-release safeguards. Make the first reconnect after a stable connection prompt (0.5 s), with exponential retry up to five seconds and reset only after ten seconds of stable connection. Reuse a 64 KiB read buffer, short-circuit control-turn selection, and avoid publishing unchanged text-only patch presentations. Coalesce immediate repeated native no-owner results with a one-second cooldown for unowned candidates with no retained conversation; positive file/client/following hints bypass it and ordinary bounded discovery remains. No new dependencies, helper, persistent cache, or conversation-content logging. Acceptance: existing counter/input/read/reconnect/idle regressions plus focused backoff/cooldown tests; before/after timing and CPU/memory/IPC measurements against isolated fixtures and read-only live probes.

## Validation and known limits

Run `./scripts/check.sh` for Core/Input assertions, offscreen animation and login-setting checks, IPC discovery/recovery, read/input transitions, idle races, missing notifications, revision resets, manual dismissal, and adversarial owner/isolation cases. Fixtures use temporary sockets and `CODEX_HOME`; they do not contact real chats.

Optimization validation on 2026-11-04 passed Core/Input, offscreen animation/accessibility/login-setting checks and all ten isolated IPC groups. Eight reconnect trials improved from 5.196 s to 0.528 s; missed completion/read recovery remained about 2.5 s. In an ABBA resource comparison (two runs per binary/workload, one synthetic root, 1,000 turns, a 650 KB snapshot, 200 ignored patches/second for eight seconds), process CPU fell 81.6%; idle CPU and peak RSS were essentially unchanged. Every resource run verified that a real state change after the burst still published without a resnapshot. Initial live catalog completion remained about ten seconds because native negative owner discovery controls that wait. The optimized app passed a final read-only live check and was installed into the existing `/Applications/Wink.app` directory with launch-at-login state preserved. Physical menu rendering and cold boot were not benchmarked.

Local observations also verified explicit start/completion transitions and matching live projections across multiple root chats. The original user confirmed the visible counts. Native click-through and the final live menu-bar animation have not been automated. The build targets macOS 13+ on Apple Silicon; Intel, notarization and other Codex versions are not validated.

The root snapshot does not expose the separate sidebar-summary fallback, Messages unread override, owned-sidechat aggregation or renderer-only submitted/skipped state. Those are source limits, not inferred statuses. Normal events are immediate; missed events need a reconciliation interval plus response time. Revision rollback/rebase can take around ten seconds. A stalled owner can take the request deadline to become unavailable.

Each frame is limited to 16 MiB. Malformed/oversized transport resets the whole connection and temporarily invalidates all counts; per-chat semantic errors are isolated. Full snapshots can be expensive for very long chats. Earlier development resource samples are not a universal idle-performance guarantee.
