# Creating and resuming a handoff

## When ending a session: record the current state

Overwrite `handoffs/current.md` with the date, the last prompt worked on, the
files created or changed (from `git status`), the checks run and their observed
results (pass, fail or not yet run), outstanding questions and decisions waiting
on the student, and the single next task.

## When resuming: verify the recorded state

Before starting new work, read `handoffs/current.md`, `README.md` and `AGENTS.md`.
Compare the recorded files with `git status` and the folder tree. Rerun the
cheapest recorded check (a version or import check, or the latest SmokeTest),
and report any difference from the record before continuing.
