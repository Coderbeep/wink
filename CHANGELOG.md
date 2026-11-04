# Changelog

## Unreleased

### Fixed

- Delayed running counts when creating chats or starting another turn in an open chat, with bounded owner-discovery retries and subscriptions that release after the owner closes its view.

### Changed

- Faster removal of read answers while Codex is in front, using globally capped read-confirmation checks that stop when unnecessary.
- Faster reconnection after a dropped Codex connection, with bounded backoff for repeated failures.
- Lower CPU work for long chat histories and streamed text updates, with fewer redundant owner lookups and unchanged counter-verification safeguards.

## 0.1.6 — prepared for the initial public release

Publication is pending. Earlier version numbers were local development builds, not previous GitHub releases.

### Added

- An opt-in **Launch at Login** menu setting, with macOS approval status and a link to Login Settings.
- A custom Wink app icon using a minimal wink mark in charcoal and muted rose.
- Native menu-bar counts for working chats, unread answers, input and approval requests, failures, and unavailable status.
- Read-only observation of existing local Codex desktop chats, with automatic read-state reconciliation and a local **Clear completed** fallback.
- Recovery from missing events, failed discovery, revision resets, owner changes, and disconnects while preserving healthy chat counts.
- Light/dark status colors, text labels, and a rotating working ring that follows Reduce Motion.
- Apple Silicon builds for macOS 13 and later, local ZIP/checksum packaging, and isolated regression checks.

The desktop connection uses an undocumented protocol. See the [README](README.md#requirements) for compatibility, privacy, installation, and current limitations.
