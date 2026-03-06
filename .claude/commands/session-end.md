# /project:end — FinTrack Session Close
#
# Run before ending every session — no exceptions.

Perform the following end-of-session steps in order:

1. **Compile session summary:**
   Review everything done since `/project:start`:
   - Tasks completed (with [x])
   - Decisions made (if any — were they logged in DECISIONS.md?)
   - Problems encountered and solutions
   - Discoveries that affect future sessions

2. **Update `.claude/PROGRESS.md`:**
   Rewrite ⚡ CURRENT STATE with:
   - Today's date and session number
   - "What was just completed"
   - "What to work on NEXT" — next 3 specific tasks, in order
   - "Active blockers" — open decisions or unresolved issues
   - "Session notes" — gotchas, API quirks, env discoveries

   Append a new SESSION LOG entry with full session detail.

3. **Mark sprint tasks** — tick [x] on all completed items in SPRINT BACKLOG.

4. **Update DECISIONS.md** if any ⚠️ OPEN decisions were resolved this session:
   Add the chosen option, rationale, and consequences.

5. **Verify:**
   Read back the updated CURRENT STATE section aloud. Confirm it's accurate.

6. **Final check:**
   - PROGRESS.md saved? ✓
   - DECISIONS.md updated if needed? ✓
   - Any `requirements-ml.txt` imports snuck in? (Flag if yes — Phase 2 only)
   - Any files left unsaved? Flag them.

End with: "Session closed. PROGRESS.md updated. Next session: `/project:start`."
