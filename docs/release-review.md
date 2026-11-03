# Initial release review

What this repo does: Wink observes local Codex desktop chats and shows their working, unread, input, approval, failure and unavailable states in the menu bar. The review assumed one Mac user with several chats, not a remote multi-user service.

## Must fix — resolved before publication

1. **An old read acknowledgement could hide a later unread answer.**
   - **What this is:** A read notification temporarily suppresses a stale unread snapshot for that answer.
   - **Problem:** Suppression previously survived canonical read confirmation. A missed later mark-unread event could leave the counter idle indefinitely.
   - **Fix:** [Canonical read confirmation](../Sources/Core.swift) now retires the acknowledgement. An existing timer also bounds unconfirmed acknowledgements and requests current owner state. Snapshot, patch and no-confirmation regressions cover both paths.
   - **Without the fix:** The same answer could stay hidden until a reconnect or another explicit read event.

2. **Removed discovery candidates caused recurring background requests.**
   - **What this is:** Lock filenames seed discovery of local desktop owners.
   - **Problem:** A candidate remained in the retry set after its lock disappeared, even when it had never been validated and had no owner.
   - **Fix:** [Successful catalog scans](../Sources/DesktopMonitor.swift) prune absent, unowned, unvalidated candidates while preserving in-flight discovery and known chats. The regression fails on the previous build and passes on the release candidate.
   - **Without the fix:** Long-running sessions would accumulate unnecessary five-second discovery requests.

## Release preparation

- Reviewed runtime reducers, socket framing, provenance checks, lifecycle cleanup, per-chat recovery, read/input precedence, menu rendering, click-through animation and Reduce Motion.
- Added a complete check entry point and pinned, read-only macOS CI. The full local check passed, including ten adversarial scenarios and real manual-dismissal lifecycle fixtures.
- Build and package paths stay inside ignored directories. The ARM64 ZIP is checked against the built executable and metadata, includes the MIT license, and has a SHA-256 checksum.
- Rewrote the README and condensed the public design record. Scanned publishable sources for private paths, live chat identifiers, credentials and conversation dumps; none were found.
- Kept the initial changelog as a prepared 0.1.6 baseline. Earlier prototypes are not represented as published releases. Source, tests, tooling and documentation are committed as separate logical changes.

Verdict: ready for the initial public source release and a draft binary release, subject to the current commit's [CI checks](https://github.com/Coderbeep/wink/actions/workflows/checks.yml).

Not checked: automated native menu interaction, other Codex versions, Intel Macs, and a macOS 13 runtime. The build targets macOS 13+; local checks ran on macOS 26.6.2. The app is ad-hoc signed, not notarized. Private protocol and transport-size limitations are documented in the README and design contract.
