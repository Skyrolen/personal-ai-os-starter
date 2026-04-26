# Daily Plan

Create your plan for today based on your goals and open tasks.

## Instructions

1. **Gather context in parallel:**
   - Read `40_Admin/goals.md` — current goals and this week's focus
   - Read `CLAUDE.md` — work preferences and schedule
   - Query Notion Tasks (c609605c-2bfc-4cf8-9762-39a8c97e4f0a) for open tasks due today or overdue
   - Check Notion Daily Log (c7017459-3ee6-4037-8aaa-f7424cf77ffe) for yesterday's "Tomorrow's top 3" if it exists

2. **Analyze and propose:**
   - What's due or overdue in Notion Tasks?
   - What did you set as tomorrow's top 3 in yesterday's Daily Note?
   - What goals need attention this week?

3. **Present today's plan.** Use AskUserQuestion to confirm:
   - **Top 3 priorities** for today (with reasoning from goals/tasks)
   - **Quick wins** — small items that can be done fast
   - **Blocked items** — things waiting on something else

4. **Save the plan** to `40_Admin/YYYY-MM-DD-daily-plan.md`:

```markdown
---
date: YYYY-MM-DD
category: daily-plan
---

# Daily Plan - [Day of week], [Date]

## Top 3 Priorities
1. [ ] [Priority 1] - Why: [connection to goal]
2. [ ] [Priority 2] - Why: [connection to goal]
3. [ ] [Priority 3] - Why: [connection to goal]

## Quick Wins
- [ ] [Small task 1]
- [ ] [Small task 2]

## Blocked
- [Item] — waiting on: [what]

## Notes
[Relevant context from goals or tasks]
```

5. **Confirm.** "Your plan for today is saved. Top priority: [#1]. Good luck!"

## Source of Truth
Tasks live in Notion Tasks database — not Tasks.md.
Tasks.md is archived and no longer updated.

## When to Use
- Every morning to set your focus
- After lunch to reset priorities
- When you feel scattered and need clarity
Type: /daily-plan
