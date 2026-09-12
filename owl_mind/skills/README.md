# Skills

Skills constrain *how* an agent should behave. The materials store answers "what
is true"; a skill answers "what this role may and may not do". They are separate
because they change for different reasons and on different schedules.

Skills are injected into the system prompt per request and hot-reload without a
restart, so a policy change does not require a deployment.

## Layout

```text
skills/
  <skill_name>/
    SKILL.md
```

## Format

```markdown
---
name: socratic_method
enabled: true
agents: [practice]          # omit or use [] to apply to every agent
keywords: [stuck, hint, help]   # optional: inject only on a keyword match
---

# Socratic Method

## Core principles
- Ask before telling.
- Hint level 3 is still not the answer.

## Never
- Emit a complete, runnable solution to an assigned problem.
```

## Planned skills

| Skill | Scope | Purpose |
| --- | --- | --- |
| `academic_integrity` | global | the line no agent crosses, whoever is asking |
| `socratic_method` | practice | progressive hinting, levels 1-3 |
| `study_planning` | planner | how a plan is structured and paced |

A skill states a policy. It does not replace enforcement: `PracticeAgent`'s
no-solutions rule is also a tool whitelist and a deterministic eval assertion,
because a rule that lives only in a prompt is a request, not a boundary.
