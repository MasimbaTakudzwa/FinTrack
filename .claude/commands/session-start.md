# /project:start — FinTrack Session Initialisation
#
# Run this at the beginning of every session before writing any code.

Perform the following session initialisation steps in order:

1. **Read the progress tracker:**
   Read `.claude/PROGRESS.md` in full, focusing on ⚡ CURRENT STATE.

2. **Check for open decisions:**
   Read `.claude/DECISIONS.md`. If any decisions are marked ⚠️ OPEN and they
   block the next tasks, surface them immediately before proceeding.

3. **Confirm orientation** — state back to the user:
   - Active sprint and goal
   - What was completed last session
   - The next 3 tasks (in order), and whether any are blocked by open decisions
   - Any active blockers
   - Current context usage (`/context`)

4. **Flag issues:**
   If PROGRESS.md has no CURRENT STATE, or if a required decision is still open,
   stop and resolve that before touching any code.

5. **Ready confirmation:**
   End with: "Ready. What should we work on first?" — or proceed to the first
   task if the user already specified one.

Do NOT write any code until step 3 is complete.
