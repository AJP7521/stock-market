# Current state: 2026-11-13

**Last prompt:** 13 · Voice about a recorded decision (complete, not committed). Prompts 1–12 are committed
(latest `ab49e95`).

## Files created or changed (Prompt 13)
- New: `backend/voice.py` (read-only record, Claude/test-double replies, sessions and turns),
  `backend/tests/test_voice.py` (8 tests), `frontend/src/VoicePanel.vue`
- Updated: `backend/app.py` (`GET /api/decisions`, `/api/voice/sessions*`), `frontend/src/App.vue`, `README.md`,
  `docs/design-pipeline.md`, `docs/verification.md`
- No packages installed or manifests changed.

## Decisions
- Claude has no audio API; the student chose Claude + browser speech (Web Speech API) over adding an OpenAI key for
  GPT-Live WebRTC. Deviation recorded: no WebRTC audio stream; Chrome sends speech audio to Google.

## Checks performed
110/110 unit tests pass. Mocked browser checks (flow, denied microphone, failed connection) pass, microphone track
released. Live session in the student's Chrome with Claude: answers for decision 3 (filled) and decision 5 (vetoed,
no trade) match SQLite. Services stopped.

## Outstanding questions
- The student needs to review and approve the Prompt 13 commit.
- The student has not yet confirmed Chrome's microphone indicator disappeared after End voice.

## Next task
Prompt 14 · Automate the workstation SmokeTest (one command).
