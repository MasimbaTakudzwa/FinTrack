# /project:checkpoint — FinTrack Mid-Session Save
#
# Run after every 2-3 completed tasks, or when context hits ~50%.

Perform a lightweight mid-session checkpoint:

1. **Update CURRENT STATE in `.claude/PROGRESS.md`:**
   - Update "What was just completed" with tasks finished since last checkpoint
   - Update "What to work on NEXT" with remaining tasks
   - Update "Active blockers" if anything changed
   - Add any API quirks or env discoveries to "Session notes"

2. **Mark completed sprint tasks** with [x] in the SPRINT BACKLOG.

3. **Check context usage** via `/context`:
   - Above 50% → warn the user and recommend `/compact`
   - After `/compact` → remind user to re-run `/project:start`

4. **Quick decision check:**
   Were any ⚠️ OPEN decisions resolved since last checkpoint?
   If yes → update DECISIONS.md now, before continuing.

5. **Brief confirmation:**
   State what was checked off and what the next task is. Keep it short.
