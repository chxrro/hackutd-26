# Agent guide

## Start here

Read [the project context](docs/project-context.md) before planning or changing code. It separates confirmed goals, the initial repository state, proposed work, and open decisions.

This is the HackUTD 2026 accessibility project: use Ray-Ban camera video to detect nearby collision risks and communicate them through a vibration belt for blind users. This is a prototype, not a validated navigation or safety system.

## Working rules

- Inspect the current tree, README, dependency manifests, and any more-specific agent instructions before working. The initial repository had no application code, so do not assume a framework, language, directory layout, or working integration.
- Keep owner-confirmed requirements separate from implementation proposals. Do not turn an open question into a fact.
- Prefer the smallest testable end-to-end experiment. Hardware and camera integration should be validated early.
- Do not assume Ray-Ban live video is accessible to the chosen phone/app. Verify the supported interface, device compatibility, permissions, and latency before building around it.
- Model fine-tuning and NVIDIA GPU rental/credits are exploratory. Start with a measurable baseline before recommending training or paid compute.
- Preserve teammates' work and existing documentation. Keep unrelated changes out of the task.
- Never commit credentials, tokens, private camera footage, or personal data. Keep example configuration free of secrets.
- Do not invent accuracy, distance estimates, latency, safety claims, or completed tests.

## Team workflow and git hygiene

Four people are coding in this repository at the same time, often with their own agents. Assume someone else is changing nearby code right now, and work so your changes merge cleanly.

- **Never work directly on `main`.** Before editing, check `git status` and the current branch. If you are on `main`, create a branch first: `git switch -c <name>/<short-topic>` (for example `alex/belt-timeout`). Start from an up-to-date `main` (`git fetch origin && git switch -c <branch> origin/main`).
- **One branch per task.** Keep branches small and short-lived. Do not pile unrelated fixes onto someone else's branch or into your own.
- **Stay in your lane.** The repo is split into components: `esp32/` (belt firmware), `hub/` (Python inference/haptic hub), `meta/` (iOS app), and `docs/`. Touch only the component your task needs. If a change has to cross components, say so up front and keep each component's changes easy to review separately.
- **Shared contracts need coordination.** `docs/protocol.md` and any message formats, ports, or pin assignments are used by more than one person. Do not change them silently. Flag the change, update the doc in the same branch, and tell the user who else is affected.
- **Do not touch work you did not create.** Leave other people's uncommitted, untracked, or in-progress files alone. Do not reformat, rename, move, or "clean up" files outside your task. Never run `git add -A`, `git add .`, `git stash`, `git reset --hard`, `git checkout -- .`, or `git clean` without explicit user approval. Stage specific paths only.
- **No history rewriting on shared branches.** Never force-push to `main` or to a branch someone else is using. Rebase or amend only your own unpushed commits.
- **Commit small and clearly.** Make focused commits with messages that state what changed and in which component (for example `hub: add stale-frame timeout`). Commit and push only when the user asks.
- **Sync often.** Pull or rebase on `origin/main` before starting and before opening a PR. If you hit a conflict in code you did not write, stop and ask instead of guessing how to resolve it.
- **Merge through pull requests.** Open a PR into `main` with a short description, the checks you ran, and anything teammates need to know (new dependencies, protocol changes, setup steps). Do not merge your own PR without the user's go-ahead.
- **Keep the tree clean.** Do not commit build outputs, virtualenvs, caches, IDE files, logs, large binaries, or local config. Add new generated paths to `.gitignore`. Delete scratch files you created before you finish.
- **Dependencies are shared too.** Add a dependency only when needed, update the matching manifest and lockfile (`hub/pyproject.toml` + `uv.lock`, `esp32/platformio.ini`, `meta/project.yml`), and mention it in the PR.

## Safety and interface design

- Treat this as an experimental collision-alert aid. Do not present it as a replacement for established mobility aids or safe to rely on without assistance.
- Keep obstacle direction separate from suggested travel direction. A vibration on the left must not ambiguously mean both "obstacle on the left" and "move left."
- Explicitly represent stale frames, disconnected hardware, unavailable inference, and uncertain detections. Silence must not be described as proof that a path is clear.
- Keep haptic commands bounded and define a safe timeout behavior before driving hardware.
- Test on a bench and in a controlled, supervised setting before any walking demo. Do not ask someone to rely on the prototype for unsupervised navigation.
- Minimize captured data and recording. Obtain appropriate consent for collection and use of real-world video.

## Validation and handoff

At initial inspection there were no build, lint, test, firmware, or run commands. Discover the current commands instead of guessing. When adding a component, document its actual setup and checks.

For each change:
1. Describe what changed and which confirmed requirement it supports.
2. Run the relevant available checks; report passed, failed, blocked, and not-run checks separately.
3. Distinguish simulation/replayed-video results from real camera, network, and belt results.
4. Update project context when a decision is made or a milestone is verified. Include evidence and the remaining limitations.

Useful test cases, once relevant components exist: left/center/right obstacles, multiple obstacles, low light, camera motion, missing or stale frames, network disconnects, low-confidence results, and belt timeouts. Select only checks relevant to the change; do not imply they already exist.
