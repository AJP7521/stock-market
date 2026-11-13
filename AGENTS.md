# Standing instructions for the coding agent

## Working rules

1. Work only inside the project root `/Users/prest/Desktop/Claude/stock-market`.
2. Read `README.md`, this file, and every file you plan to change before editing.
   Trace the inputs and outputs of the code you are about to touch.
3. Preserve existing work. Inspect a file or folder before moving, overwriting or deleting it.
4. Show the changes (`git status`, `git diff`) for student review **before any commit**.
   Never commit or push without the student's approval.
5. Save each major prompt under `prompts/`, continuing the existing numbering
   (`NN-short-name.md`, with letter suffixes such as `09a` for sub-steps).
   Record its checks and observed results in `docs/verification.md`.
6. Keep credentials (API keys, tokens, passwords) out of source code, prompts,
   logs and Git. Read them from the backend environment (e.g. an ignored `.env`).
7. Market data and trading are simulated. Label synthetic, test and recorded data
   as such wherever it is shown or stored. Keep live-provider and mocked
   observations separate in the documentation.
8. Update `docs/design-pipeline.md` when a component is added or its data flow changes. Its "SQLite schema"
   section lists every table; update it whenever a table or column is added or changed.
9. At the end of a session, follow `handoffs/create-handoff.md`.

## Tools and packages: CHECK → TAKE ACTION → VERIFY

Use this procedure whenever a tool or package is needed.

**CHECK.** Inspect the operating system, the active Python interpreter
(`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`), Node.js, the
package managers, dependency files and lockfiles. Find out which tools and packages
are already available. Reuse the existing Python environment. Do not create
or recreate a virtual environment by default.

**TAKE ACTION.** For each missing dependency the current task needs, state:
- its purpose
- a compatible version
- the exact command
- where it will be installed (system Python 3.14 site-packages, or `frontend/node_modules`)
- the files it will change (e.g. `backend/requirements.txt`, `frontend/package.json`,
  `frontend/package-lock.json`)

Wait for student approval before installing software or changing a dependency
manifest or lockfile.

**VERIFY.** Run the smallest relevant version, import or build check in the
intended environment. Record the command, expected result and observed result in
`docs/verification.md`. List any check that still needs to be run.

## SmokeTest

SmokeTest is a repeatable check that runs **without source edits**.

- Once a feature has a UI, SmokeTest must start the local services (FastAPI and
  Vue), open a real browser, perform a user operation, and compare the visible
  result with the expected result.
- Where relevant, also check the stored data (SQLite).
- It uses an isolated database and deterministic or stubbed external inputs, so
  results can be repeated.
- It runs from one documented command, recorded in `README.md` and
  `docs/verification.md`.
- Record failures as well as passes, with the evidence observed.
- It stops every process and browser it started, including after a failure.

## AutoLoop

AutoLoop is used when a check fails:

1. Inspect the failure: output, logs, screenshots and stored data.
2. Make the smallest correction that stays in scope.
3. Rerun the failed check.

Stop when the check passes, after **five** correction cycles, or when further
permission is needed (installing software, changing dependencies, widening scope,
or any action that needs approval under these rules). Report each cycle in
`docs/verification.md`.
