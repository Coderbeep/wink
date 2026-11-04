<p align="center">
  <img src="Resources/Wink.png" width="96" height="96" alt="Wink app icon">
</p>

<h1 align="center">Wink</h1>

<p align="center">
  <a href="https://github.com/Coderbeep/wink/actions/workflows/checks.yml"><img src="https://github.com/Coderbeep/wink/actions/workflows/checks.yml/badge.svg?branch=main" alt="Checks"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-B86F70" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/macOS-13%2B-282A2E?logo=apple&amp;logoColor=white" alt="macOS 13 or later">
  <img src="https://img.shields.io/badge/architecture-Apple%20Silicon-282A2E" alt="Apple Silicon">
</p>

A small macOS menu-bar companion for Codex desktop. See which chats are working, have an unread answer, or need your attention without keeping Codex in front.

Wink is an independent project, not an official OpenAI app. It uses system frameworks only: one native executable, no helper process, account, API key, or package dependencies.

## Requirements

- **Apple Silicon Mac** running **macOS 13 or later**. Intel builds are not provided.
- Codex desktop running locally under the same macOS user.
- To build: Apple's Command Line Tools or Xcode. Python 3.8 or later is needed for packaging and the full test suite; no third-party Python packages are required.

The current adapter was verified against desktop build **26.1027.61615**, using its private local IPC protocol. Compatibility with future Codex updates is not guaranteed.

## Build and install

```sh
git clone https://github.com/Coderbeep/wink.git
cd wink
./build.sh
open build/Wink.app
```

You can also move `build/Wink.app` into Applications. Quit an existing Wink copy before replacing it. Wink has no Dock icon or normal window; use **Quit Wink** in its menu to stop it.

Choose **Launch at Login** in the menu to start Wink automatically when you sign in. This is off by default; its checkmark reflects macOS’s current setting. **Needs Approval** means you must allow it through **Open Login Settings…**. Toggle it off to remove the login registration. Keep Wink in a stable location, such as Applications, before enabling it.

The build is **ad-hoc signed and not notarized**. A downloaded copy may be blocked by macOS. If you trust its source and integrity, follow Apple's per-app **Privacy & Security → Open Anyway** process; see [Safely open apps on your Mac](https://support.apple.com/en-us/102445). Do not disable Gatekeeper globally.

Published downloads, when available, appear under [Releases](https://github.com/Coderbeep/wink/releases). To produce a local distributable:

```sh
./scripts/package.sh
```

This creates `dist/Wink-macos-arm64.zip` and `dist/SHA256SUMS`. Verify a downloaded archive against the accompanying checksum before opening it.

## Read the indicators

Each symbol has a count and a text label in the menu. Colors follow Codex's default palette and adapt to light and dark appearance.

| Indicator | Meaning |
| --- | --- |
| Rotating neutral ring | Working chats. The ten small marks stay still when Reduce Motion is enabled. |
| Blue check circle | Unread answers. Opening or marking an answer read in Codex clears its Wink entry. |
| Blue question bubble | Input needed. |
| Green hand | Approval needed. |
| Red cross circle | A failed chat. |
| Gray question circle | That chat's status is unavailable. |
| `Wink ?` | The desktop connection or initial status discovery is incomplete. |

The menu lists chat titles and provides **Open Codex**, **Clear completed**, **Launch at Login**, and **Quit Wink**. **Clear completed** dismisses ready entries in Wink only; it never marks chats read in Codex. This local dismissal is held in memory and resets when Wink quits.

## Tracking and privacy

Wink observes the desktop app's existing local socket and uses session-lock filenames to discover background chats. Filenames and quiet time never establish a chat's status. CLI sessions and internal subagents are excluded. The default location is `~/.codex`; `CODEX_HOME` is respected when set in Wink's environment.

Codex can omit a read notification or final state update. Wink therefore reconciles non-idle chats every **five seconds**, checks idle chats every **thirty seconds**, and retries missing owners. Normal events update the display immediately. While Codex is in front and answers are ready, Wink makes at most one additional read-confirmation request per second across all chats. This check stops in the background or when no answers are ready; opening Wink’s menu can also request a confirmation. Entries clear only after Codex reports them read. Missing or incompatible evidence becomes unavailable; one unresponsive chat does not invalidate healthy chats.

The private protocol briefly sends full conversation snapshots through memory. Wink retains titles and derived status metadata, discards message and command content, and saves no conversation or status history to disk. It makes no internet requests, reads no authentication files, and sends no prompts. It does not answer approvals, resume or stop chats, mark them read, or change Codex configuration or hooks.

### Limits

The adapter covers **local desktop-owned root chats**. Remote/cloud-only chats, Messages-backed unread overrides, and Codex's separate sidebar-summary or owned-sidechat aggregation are not covered by this source. Renderer-local submitted/skipped question state may differ until an accepted reply or canonical state update arrives. Unsupported runtime or authentication metadata is shown unavailable rather than guessed.

Malformed or oversized (over 16 MiB) protocol frames reset the connection and temporarily invalidate all counts.

A missed notification can take a reconciliation interval plus response time to repair. Read acknowledgement is temporary; ambiguous same-answer changes can take about ten seconds to reconcile. These are recovery bounds, not a promise of instantaneous or complete parity with every Codex surface. See [the signal contract and validation record](docs/features/wink.md) for details.

## Development and checks

```sh
./scripts/check.sh
```

The full check builds Wink and runs state/parser assertions, offscreen animation, Reduce Motion and login-setting state/error checks, and isolated IPC fixtures covering discovery, read/input transitions, lost events, revision resets, owner changes, reconnects, and manual dismissal. Fixtures use temporary `CODEX_HOME` directories and do not contact real chats.

The approved icon artwork is in `Resources/Wink.svg`, with a 1024-pixel PNG master. After exporting artwork changes to `Resources/Wink.png`, run `./scripts/make-icon.sh` to regenerate the standard macOS icon sizes. Builds use the checked-in `Wink.icns`.

For just the core assertions, run `./test.sh`. For a read-only live diagnostic:

```sh
build/Wink.app/Contents/MacOS/Wink --probe 15
```

The diagnostic prints chat IDs and phases, then unsubscribes. It does not print message bodies or titles. Redact IDs before sharing output, and do not share raw Codex snapshots or authentication files.

When reporting an issue, include the Wink and Codex versions, expected and observed counts, and whether reconnecting changes the result. A status example is more useful than a conversation transcript.

## License

[MIT](LICENSE) — copyright 2026 Coderbeep.
