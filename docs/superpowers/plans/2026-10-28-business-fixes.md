# Business correctness fixes implementation plan

**Status (2026-10-28):** Implementation and offline verification complete. 821 tests passed; real voice/device and full process startup acceptance remains manual. Evidence: `../reports/2026-10-28-business-fixes.md`. Checkboxes remain unchanged under this repository's human-acceptance convention.

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Fix the four confirmed audit defects while preserving wake-word, ambient, and typed conversations.
**Architecture:** Restrict deterministic voice shortcuts to unambiguous input; validate launcher health configuration before reuse; reject malformed/cross-origin device writes at the HTTP boundary. Keep existing model, catalog validation, and device confirmation as execution authority.
**Tech Stack:** Python, pytest, PowerShell, standard-library HTTP.
**Spec:** User-approved project audit immediately preceding “开始修改业务代码”.

## Constraints
- Work in E:/smart-home, preserving existing uncommitted work. No Unity changes, secrets edits, live API calls, or real device/microphone/camera tests.
- The approved implementation is executed directly; no additional design approval is needed.
- No automatic commits in this already dirty checkout.

## Review focus
- Follow-up negation or a new action must discard the pending comfort need.
- Pure room answers retain their no-extra-model-call behavior.
- Mixed relative/absolute instructions must preserve each tool's own parameters.
- Healthy but differently configured existing services must fail startup validation.
- Invalid HTTP bodies and browser origins must never reach the device application; normal local clients must keep working.

## Tasks
- [ ] Voice: add failing regressions in test_comfort_actions.py and test_agent_session.py; restrict pending-room completion and relative-temperature rewriting in agent_session.py; run the voice suite.
- [ ] Launcher: add a testable health-configuration check, exercise it through PowerShell with controlled health data, and invoke it on both reuse paths and newly launched services.
- [ ] Home HTTP: add isolated HTTP regression tests, implement Host/Origin/media type/length/object checks before application dispatch, inspect local write-auth feasibility and wire all clients if needed; run home and console suites separately.
- [ ] Review: review only this turn's diff against a saved pre-edit snapshot, address actionable findings, record fresh validation and remaining hardware-only checks.
