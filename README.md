# Owl Mind

A multi-agent computer-science study assistant: five specialised agents behind a routing
layer, with real Model Context Protocol tool access and end-to-end token accounting.

| Agent | Owns | Hard boundary |
| --- | --- | --- |
| ConceptAgent | explanations, analogies, worked examples | cites materials; never invents APIs |
| PracticeAgent | Socratic hints on problems | **never emits a complete solution** |
| PlannerAgent | study plans, spaced repetition | scheduling is arithmetic, never LLM-guessed |
| QuizAgent | quiz generation and grading | grading must be reproducible |
| TutorHandoffAgent | escalation to a human TA | **no LLM call** -- works during a model outage |

## Documents

| File | Purpose |
| --- | --- |
| [STUDY-ASSISTANT-PLAN.md](STUDY-ASSISTANT-PLAN.md) | architecture, agent roster, MCP topology, day plan |
| [owl_mind_dev_workflow.md](owl_mind_dev_workflow.md) | issue / branch / PR / review SOP |
| [issues/](issues/) | issue specifications, one file per issue |

## Status

Scaffolding. See [issues/ISSUE-001-project-scaffold.md](issues/ISSUE-001-project-scaffold.md).
Run instructions land with that issue.
