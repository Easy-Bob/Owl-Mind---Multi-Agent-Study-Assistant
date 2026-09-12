---
name: Task
about: Technical work with no direct user-facing story
title: "[Task] "
labels: task
---

## Summary

## Background / Business Context

## User Story

As a <role>,
I want <goal>,
so that <benefit>.

## Functional Requirements

- [ ]
- [ ]

## Non-Functional Requirements

- [ ] No production change before PR approval.
- [ ] No credentials, API keys, or `.env` files committed.
- [ ] Code readable enough for SOP/onboarding use.
- [ ] Existing behaviour not broken.

## Acceptance Criteria

Given <starting condition>,
when <action occurs>,
then <expected result>.

## Test Notes

- [ ] `pytest` green.
- [ ] Tested normal case.
- [ ] Tested failure / missing-data case.

## Out of Scope

-

## Risks

- Data risk:
- Workflow risk:
- Deployment risk:
- Rollback concern:

## Dependencies

- Related issue:
- Related PR:
- Required access:
- Required environment config:

## Definition of Done

- [ ] Requirement implemented and checkboxes checked.
- [ ] Code committed, pushed, PR opened against `develop`.
- [ ] PR links the Issue using `Closes #<number>`.
- [ ] Local/dev testing completed.
- [ ] Reviewer approved and PR merged.
- [ ] Issue closed and project card moved to Done.
- [ ] README/SOP updated if the workflow changed.
- [ ] Dependency connectivity verified from inside the API container.
- [ ] Logs reviewed for leaked API keys and unexpected errors.
