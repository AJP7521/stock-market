# Current state: 2026-11-13

**Last prompt:** 14 · Workstation SmokeTest (complete and committed). All prompts in `prompts/` (01–14, with 07a and
09a–09d, 10a) are committed. Latest commit `290e1f0` (records the live microphone release for Prompt 13).

## Files created or changed
`git status`: clean — nothing uncommitted. This session's commits:

| Commit | Prompt | Main files |
|---|---|---|
| `338df1b` | 10 Decision API | `backend/decisions.py`, `series.py`, `settings.py`, `.env.example`, `requirements.txt` (`anthropic==2.8.0`) |
| `84eff64` | 10A Ask about chart | `backend/chart_images.py`, `frontend/src/AskChartPanel.vue`, `CandleChart.vue` (`capture()`) |
| `f778a41` | 11 Agent review | `backend/review.py` |
| `ab49e95` | 12 Paper automation | `backend/automation.py`, `backend/paper.py` (decision_id, pre-fill hooks), `frontend/src/AutomationPanel.vue` |
| `8e646c6`, `290e1f0` | 13 Voice | `backend/voice.py`, `frontend/src/VoicePanel.vue` |
| `6b42471` | 14 SmokeTest | `backend/smoke.py`, `backend/requirements-dev.txt` (`playwright==1.65.1`), `.github/workflows/smoke.yml` |

Each prompt also updated `README.md`, `docs/design-pipeline.md`, `docs/verification.md` and its tests in
`backend/tests/`. Local only (ignored): `.env` with `DECISIONS_PROVIDER=claude` and `ANTHROPIC_API_KEY`;
`test-results/` (SmokeTest reports).

Installed in system Python 3.14 (student-approved): `anthropic` 2.8.0, `playwright` 1.65.1 (+ greenlet 3.5.6).

## Decisions made with the student
- **Provider:** Claude (`claude-opus-5-5`) is the default; OpenAI stays optional (`DECISIONS_PROVIDER=openai`) and
  has never been called live (no OpenAI key).
- **Voice (Prompt 13):** the Claude API has no audio, so speech uses the browser's Web Speech API in Google Chrome
  instead of GPT-Live WebRTC. Recorded as a deviation; Chrome sends speech audio to Google.
- **Automation on test data** trades only in the disposable `paper-test` account.

## Checks performed (latest results)
| Check | Command | Result |
|---|---|---|
| Unit tests | `python3 -m unittest discover -s backend/tests -t .` | **110 tests, OK** (rerun 2026-11-13 for this handoff) |
| SmokeTest | `python3 -m backend.smoke` | **PASS 48/48** twice in a row (`smoke-20261112-230417`, `-230450`), clean account each time; earlier runs 1–2 failed on test-side issues, fixed by AutoLoop cycles 1–2 |
| Live Claude decision | Prompt 10 | buy on TESTSMA test data, matched SQLite |
| Live Claude image + review | Prompts 10A, 11 | buy / up / sufficient; reviewer accept; matched SQLite |
| Live voice | Prompt 13, student's Chrome | answers for decision 3 (filled) and 5 (vetoed) matched SQLite; microphone icon went away after End voice |
| GitHub Actions | `.github/workflows/smoke.yml` | **not run** (no Git remote) |

No services are running (ports 8000 and 5173 free).

## Outstanding questions
- **GitHub:** the repository has no remote. Pushing (which needs the student's approval) is required before the
  SmokeTest workflow can run; its browser path (Playwright's own Chromium) is untested locally.
- **Pre-existing:** two simultaneous first connections to a brand-new database can fail once with
  "database is locked" (`recorder.connect`, `PRAGMA journal_mode=WAL`). Seen once at start-up in Prompt 12; it did not
  affect any check. Fixing it is a small scoped change if wanted.
- Not exercised: a live OpenAI call, a real microphone-permission denial in Chrome (mocked only), automation on live
  Yahoo data, live provider timeouts/refusals (mocked only).

## Next task
No prompts remain in `prompts/`. Suggested single next task: add a GitHub remote and push (with approval) so the
SmokeTest workflow runs on GitHub, then record its result in `docs/verification.md`.
