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
