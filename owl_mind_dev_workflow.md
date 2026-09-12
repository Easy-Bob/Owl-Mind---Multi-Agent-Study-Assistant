# Owl Mind Management and Development Workflow SOP

## 1. Purpose

This SOP defines the standard development workflow for SpecwinOrchard work. It explains how a requirement should move from a business request to a GitHub Issue, GitHub Project card, feature branch, Pull Request, review, merge, and deployment planning.

The goal is to prevent untracked changes, direct production edits, unclear requirements, and code changes that cannot be reviewed later.

---

## 2. Core Principle

Every meaningful change should be traceable through this chain:

```text
Business Need
  → User Story / Requirement
  → GitHub Issue
  → GitHub Project Backlog Card
  → Feature Branch
  → Code Change
  → Pull Request
  → Code Review
  → Merge
  → Issue Auto-Close
  → Deployment / Release Note
```

Do not start from code unless the change is a small emergency fix. Even then, create the Issue afterward and link the Pull Request.

---

## 3. Standard Workflow Summary

Use this sequence for normal development:

1. Create GitHub Issue.
2. Write User Story, Functional Requirements, Non-Functional Requirements, Acceptance Criteria, Test Notes, and Definition of Done inside the Issue.
3. Add the Issue to the GitHub Project backlog. (Or actually you can create Issue directly from GitHub Project kanban board backlog column)
4. Assign attributes.
5. Developer creates a branch belongs to the Issue.
6. Developer implements and tests locally or in dev.
7. Developer pushes the branch.
8. Developer opens Pull Request. (Actually you can open a PR when you just start to work on the code; the final PR will show to the team only when you finished step 9 and assigned a reviewer)
9. PR description includes `Closes #issue_number`, `Fixes #issue_number`, or `Resolves #issue_number`.
10. Reviewer reviews code and leaves comments.
11. Developer updates the branch and replies to review comments.
12. PR is approved and merged.
13. Linked Issue auto-closes after merge.
14. GitHub Project card moves to Done or is manually moved if automation does not trigger.
15. Deployment steps are documented before production change.

---

## 4. GitHub Project Kanban Columns

Recommended columns for SpecwinOrchard:

| Column      | Meaning                                                          |
| ----------- | ---------------------------------------------------------------- |
| Backlog     | Accepted work that is not ready to start yet.                    |
| Ready       | Requirements are clear enough for development.                   |
| In Progress | Developer is actively working on it.                             |
| In Review   | Pull Request is open and waiting for review.                     |
| Done        | Merged, verified, documented, and no further action is required. |

---

## 5. GitHub Project Fields

Recommended custom fields:

| Field              | Example Values                                               | Purpose                           |
| ------------------ | ------------------------------------------------------------ | --------------------------------- |
| Status             | Backlog, Ready, In Progress, Review, Done                    | Kanban column/state.              |
| Priority           | P0, P1, P2, P3                                               | Business urgency.                 |
| Type               | Story, Bug, Task, Chore, Spike, Documentation                | Classifies the work.              |
| Area               | Validation, Automation, Portal, Database, DevOps, Compliance | Functional ownership.             |
| Sprint / Iteration | Sprint 2026-05-A                                             | Planning cycle.                   |
| Milestone          | Audit Phase 1                                                | Release grouping.                 |
| Owner              | Developer name                                               | Person responsible for execution. |
| Reviewer           | Reviewer name                                                | Person responsible for approval.  |

Priority guidance:

| Priority | Meaning                                                  |
| -------- | -------------------------------------------------------- |
| P0       | Production outage, security issue, data corruption risk. |
| P1       | Business-critical workflow blocked.                      |
| P2       | Important improvement or defect with workaround.         |
| P3       | Nice-to-have, cleanup, refactor, documentation.          |

---

## 6. Issue Types

Use these categories consistently.

### 6.1 User Story

Use for business-facing functionality.

Example:

```text
As a Lab Director,
I want to view a complete audit log of all validation runs, user logins, and cache operations,
so that I can demonstrate CLIA/CAP compliance during inspections and investigate any discrepancies.
```

### 6.2 Bug

Use when something expected is not working.

Example:

```text
Audit log entries are missing the acting username when a validation run is triggered by an analyst role user.
```

### 6.3 Task

Use for technical work with no direct user story.

Example:

```text
Add audit log write calls to the cache invalidation endpoint so every cache clear event is recorded with user, timestamp, and affected date range.
```

### 6.4 Chore

Use for maintenance.

Example:

```text
Add comments to existing client script for SOP readability.
```

### 6.5 Spike

Use for investigation.

Example:

```text
Investigate whether audit log entries should be written to SQL Server directly or buffered through the Flask API logging layer before persistence.
```

### 6.6 Documentation

Use for SOP, README, deployment notes, or onboarding updates.

Example:

```text
Document the audit log schema, retention policy, and how to pull audit reports for CLIA/CAP inspector review.
```

---

## 7. Standard Issue Template

Use this template for most SpecwinOrchard development work.

```md
## Summary

Short description of the requested change.

## Background / Business Context

Explain why this change is needed and what business process it supports.

## User Story

As a <role>,
I want <goal>,
so that <benefit>.

## Functional Requirements

- [ ] Requirement 1
- [ ] Requirement 2
- [ ] Requirement 3

## Non-Functional Requirements

- [ ] No production change before PR approval.
- [ ] Logic must be version-controlled in `specwinorchard2026`.
- [ ] No credentials, patient data, database dumps, or `.env` files committed.
- [ ] Code should be readable enough for SOP/onboarding use.
- [ ] Existing validation pipeline behavior must not be broken.

## Acceptance Criteria

- [ ] Observable pass/fail condition 1
- [ ] Observable pass/fail condition 2
- [ ] Observable pass/fail condition 3

## Test Notes

- [ ] Tested locally in the SpecwinOrchard dev environment.
- [ ] Tested with normal case.
- [ ] Tested with missing/blank data.
- [ ] Tested with duplicate data.
- [ ] Screenshots or command output attached if useful.

## Risks

- Data risk:
- Workflow risk:
- Deployment risk:
- Rollback concern:

## Dependencies

- Related issue:
- Related PR:
- Required access:
- Required environment config or feature flag:

## Definition of Done

- [ ] Requirement implemented and checked checkboxes.
- [ ] Code committed to feature branch.
- [ ] Code pushed to GitHub.
- [ ] Pull Request opened.
- [ ] PR links the Issue using `Closes #<number>`, `Fixes #<number>`, or `Resolves #<number>`.
- [ ] Local/dev testing completed.
- [ ] Reviewer approved the PR.
- [ ] PR merged into the target branch.
- [ ] Issue auto-closed or manually closed with explanation.
- [ ] Project card moved to Done.
- [ ] SOP/README updated if the workflow changed.
- [ ] Changes are tested locally.
- [ ] Database access is verified with SQL authentication to ensure Docker/Linux compatibility.
- [ ] Logs are reviewed before release for PHI leakage and unexpected errors.
```

---

## 8. Example Issue: Audit Log for Validation Runs and User Actions

```md
# [Story] Audit Log — Validation Run and User Action Tracking

## Summary

Implement a persistent audit log that records every validation run, user login/logout, role change, and cache operation, so that lab management and compliance officers can review a full activity trail at any time.

## Background / Business Context

SpecwinOrchard operates under CLIA/CAP regulatory requirements. During CAP inspections, the lab must demonstrate that all automation validation results were reviewed by qualified personnel and that access to the system was controlled and traceable. Currently, user actions and validation events are written only to rotating log files and are not queryable by non-technical staff. A structured, persistent audit log stored in SQL Server will allow Lab Directors and compliance officers to pull activity reports without developer involvement.

## User Story

As a Lab Director,
I want to view a queryable audit log of all validation runs, user logins, role assignments, and cache operations,
so that I can demonstrate to CAP inspectors that all system actions were performed by authorized personnel and every automation result was reviewed before release.

## Functional Requirements

- [ ] Every validation run is recorded with: triggered_by (username), run_timestamp, date_range_start, date_range_end, biomarker_count, run_status (success / partial / failed).
- [ ] Every user login and logout is recorded with: username, role, session_start, session_end, ip_address.
- [ ] Every cache clear operation is recorded with: cleared_by (username), cleared_at, affected_date_range, cache_type.
- [ ] Every role assignment or role change is recorded with: changed_by, target_user, old_role, new_role, changed_at.
- [ ] Audit records are written to a dedicated `AuditLog` table in SQL Server (`SpectrawinOrchard` database).
- [ ] Admin role users can query the audit log from the portal UI with filters: date range, event type, username.
- [ ] Audit log is read-only from the UI; no edit or delete capability is exposed.
- [ ] Audit records must not be deleted by cache clear operations.

## Non-Functional Requirements

- [ ] Audit writes must not block the main validation pipeline; use async or post-commit writes.
- [ ] Code must be reviewed before merge.
- [ ] No production deployment before approval.
- [ ] No patient data or PII written to audit fields beyond what is defined in the schema above.
- [ ] Existing validation pipeline behavior must not be changed.
- [ ] Audit log table must be included in the database backup policy.

## Acceptance Criteria

Given a logged-in analyst user triggers a validation run,
when the run completes (success or failure),
then an audit record appears in the AuditLog table with the correct username, timestamp, date range, and status.

Given a user logs in and later logs out,
when the Lab Director queries the audit log by username,
then both the login and logout events appear with accurate timestamps and IP address.

Given an admin clears the cache for a date range,
when the Lab Director queries cache events,
then the record shows who cleared the cache, when, and which date range was affected.

Given a non-admin user accesses the audit log UI panel,
when the page loads,
then the audit log panel is hidden or returns a 403 Forbidden response.

## Test Notes

- [ ] Trigger a validation run as analyst user; confirm audit record created.
- [ ] Trigger a failed validation run; confirm failure status is recorded, not omitted.
- [ ] Log in and log out; confirm both events recorded with correct session timestamps.
- [ ] Clear cache as admin; confirm cache audit record created with correct date range.
- [ ] Attempt to access audit log as viewer role; confirm access denied.
- [ ] Confirm audit table rows are not deleted when cache is cleared.
- [ ] Confirm audit write failure does not crash the validation pipeline (graceful degradation).

## Risks

- Data risk: Audit log may capture usernames; ensure no patient identifiers are written.
- Workflow risk: Async audit writes may miss events if the process exits unexpectedly; document acceptable loss tolerance.
- Deployment risk: Requires `AuditLog` table migration on SQL Server before deployment.
- Rollback concern: If rolled back, audit table can remain in place; application code will simply stop writing to it.

## Dependencies

- Related issue: Role-based access control implementation (must be merged first for role field to be reliable).
- Related PR: —
- Required access: SQL Server write access for the service account used by Docker containers.
- Required environment config or feature flag: `AUDIT_LOG_ENABLED=true` in `.env`.

## Definition of Done

- [ ] Requirement implemented and checked checkboxes.
- [ ] Code committed to feature branch.
- [ ] Code pushed to GitHub.
- [ ] Pull Request opened.
- [ ] PR links the Issue using `Closes #<number>`, `Fixes #<number>`, or `Resolves #<number>`.
- [ ] Local/dev testing completed.
- [ ] Reviewer approved the PR.
- [ ] PR merged into the target branch.
- [ ] Issue auto-closed or manually closed with explanation.
- [ ] Project card moved to Done.
- [ ] SOP/README updated if the workflow changed.
- [ ] Changes are tested locally.
- [ ] Database access is verified with SQL authentication to ensure Docker/Linux compatibility.
- [ ] Logs are reviewed before release for PHI leakage and unexpected errors.
```

---

## 9. Functional Requirements vs Acceptance Criteria

These are not the same.

### Functional Requirements

Functional Requirements define what the system must do.

Example:

```text
The system must write an audit record to the AuditLog table when a validation run is triggered.
```

### Acceptance Criteria

Acceptance Criteria define how the reviewer knows the requirement is complete.

You can write this intuitively; However, there is antother practice for you - it's very similar with how to write a User Story:

With this we can even assign other team's dev to review.

Example:

```text
Given an analyst user triggers a validation run,
when the run completes successfully,
then an audit record exists in AuditLog with the correct username, timestamp, and run status.
```

Recommended format:

```text
Given <starting condition>,
when <action occurs>,
then <expected result>.
```

---

## 10. Non-Functional Requirements

Non-Functional Requirements define quality, safety, maintainability, and operational constraints.

For SpecwinOrchard, common NFRs include:

```md
- Code must be tracked in GitHub.
- Code must be readable by future developers and AI agents.
- Code must not expose credentials, vendor files, production data, or database dumps.
- Production changes require backup and approval.
- Existing standard behavior must remain intact unless the Issue explicitly requests an override.
- UI/database customizations should be exported as fixtures if they need to be version-controlled.
```

---

## 11. Definition of Ready

An Issue is Ready when it has enough information for a developer to start.

```md
## Definition of Ready

- [ ] Business purpose is clear.
- [ ] User Story or technical objective is written.
- [ ] Functional Requirements are listed.
- [ ] Acceptance Criteria are testable.
- [ ] Priority is assigned.
- [ ] Owner is assigned.
```

Do not move an Issue from Backlog to Ready if the developer still has to guess the business requirement.

---

## 12. Definition of Done

A work item is Done only when all applicable items are complete.
Notice: We do not modify the DoD, all Issue will follow the same DoD.

```md
## Definition of Done

- [ ] Requirement implemented.
- [ ] Code committed to feature branch.
- [ ] Code pushed to GitHub.
- [ ] Pull Request opened.
- [ ] PR links the Issue using `Closes #<number>`, `Fixes #<number>`, or `Resolves #<number>`.
- [ ] Local/dev testing completed.
- [ ] Reviewer approved the PR.
- [ ] PR merged into the target branch.
- [ ] Issue auto-closed or manually closed with explanation.
- [ ] Project card moved to Done.
- [ ] SOP/README updated if the workflow changed.
- [ ] Changes are tested locally.
- [ ] Database access is verified with SQL authentication to ensure Docker/Linux compatibility.
- [ ] Logs are reviewed before release for PHI leakage and unexpected errors.
```

For production-impacting changes, add:

```md
- [ ] Server Backup completed before production deployment.
- [ ] Rollback plan documented.
- [ ] Post-deployment verification completed.
```

For document changes, add:

```md
- [ ] README.md, dev-readme.md, and CHANGELOG/version history are updated for release-impacting/new feature changes.
- [ ] Local deployment instructions are verified and working. Screenshot or Teams Meeting recording should be attached.
- [ ] New modules, Docker/build changes, dependencies, seed data, and new AppSettings/.env variables are documented.
```

For API contract changes, add:

```md
- [ ] New or changed endpoints use Pydantic request/response models.
- [ ] Swagger/ReDoc show accurate parameters, response types, and status codes.
- [ ] Responses are bound to strict response schemas to avoid exposing raw database fields.
```

For Security & Compliance changes, add:

```md
- [ ] Authenticated routes declare explicit RBAC permission dependencies.
- [ ] Sensitive actions create audit logs showing user/role, action, timestamp, and success/failure.
- [ ] No PHI or patient identifiers appear in URLs, terminal logs, file logs, SSE streams, or error tracebacks.
- [ ] Auth changes preserve session timeout, failed-login lockout, and admin-only unlock behavior.
```

---

## 13. Branching Model

Recommended branch structure:

```text
main
  └── develop
        └── feature/<issue-number>-short-description
        └── bugfix/<issue-number>-short-description
        └── chore/<issue-number>-short-description
```

Use `main` for stable production-ready code.

Use `develop` for integration before production release.

Use feature branches for all normal development.

Examples:

```bash
git checkout develop
git pull origin develop
git checkout -b feature/12-po-email-recipients
```

```bash
git checkout develop
git pull origin develop
git checkout -b bugfix/18-po-email-cc-not-populating
```

```bash
git checkout develop
git pull origin develop
git checkout -b docs/25-github-workflow-sop
```

---

## 14. Commit Message Rules

Use clear commit messages.

Recommended format:

```text
<type>: <short summary>
```

Examples:

```text
feat: add AuditLog table migration and write service
fix: ensure audit record is written on failed validation run
docs: add GitHub project workflow SOP
chore: add docstrings to audit log service methods
test: add audit log write coverage for cache clear endpoint
```

For issue traceability, include the Issue number when useful:

```text
fix: capture session_end timestamp on forced logout (#12)
```

---

## 15. Pull Request Rules

Every meaningful code change should use a Pull Request.

### PR Title Format

```text
[Issue #12] Add audit log for validation runs and user actions
```

or:

```text
Fix missing audit record on failed validation run
```

### Required PR Description

Use this template:

```md
## Summary

Briefly describe what changed.

## Linked Issue

Closes #12

## Type of Change

- [ ] Feature
- [ ] Bug fix
- [ ] Chore
- [ ] Documentation
- [ ] Refactor
- [ ] Test

## What Changed

-
-
-

## Testing

- [ ] Tested locally in the SpecwinOrchard dev environment.
- [ ] Tested normal case.
- [ ] Tested missing data case.
- [ ] Tested duplicate data case.

## Screenshots / Evidence

Attach screenshots, console output, or before/after behavior if useful.

## Deployment Notes

- Requires bench migrate: Yes / No
- Requires bench build: Yes / No
- Requires fixture export/import: Yes / No
- Requires production backup: Yes / No
- Production deployment steps:

## Risk / Rollback

- Risk:
- Rollback:
```

---

## 16. `Closes #issue_number` vs `Fixes #issue_number`

GitHub recognizes closing keywords in PR descriptions and sometimes commit messages.

Use:

```text
Closes #12
```

or:

```text
Fixes #12
```

or:

```text
Resolves #12
```

Recommended practice:

- Use `Closes #12` for a feature/story/task.
- Use `Fixes #12` for a bug.
- Use `Resolves #12` for a general issue.

Example PR description:

```md
## Linked Issue

Closes #12
```

When the PR is merged into the default branch, GitHub should auto-close the linked Issue.

Important:

- The keyword must reference the correct Issue number.
- The PR must merge into the repository's default branch or configured base branch for auto-close behavior.
- GitHub Project card movement may require separate Project workflow automation.
- Auto-closing the Issue does not always automatically move the card to Done unless the Project workflow is configured.

---

## 17. GitHub Project Automation

Recommended GitHub Project workflows:

| Event                          | Automation                                           |
| ------------------------------ | ---------------------------------------------------- |
| Issue added to project         | Set Status = Backlog                                 |
| Issue assigned                 | Optional: Set Status = Ready                         |
| Pull Request opened and linked | Set Status = In Review                               |
| Pull Request merged            | Set Status = Done                                    |
| Issue closed                   | Set Status = Done                                    |
| PR review requested            | Set Status = In Review                               |
| PR changes requested           | Keep Status = In Review or move to Blocked if needed |

If `Closes #12` closes the Issue but the Project card does not move, check:

1. Is the Issue actually added to the correct GitHub Project?
2. Is the Project using the correct Status field?
3. Does the Project have a workflow configured for “Item closed” or “Pull request merged”?
4. Did the PR merge into the default branch?
5. Was the closing keyword placed in the PR description, not only a random comment?
6. Is the linked Issue from the same repository or another repository?

---

## 18. Code Review Workflow

### 18.1 Reviewer Responsibilities

Reviewer should check:

```md
- [ ] Does the owner include changed files that is NOT belongs to this Issue?
- [ ] Does the owner made unnecessary changes in changed files like import unused packages?
- [ ] Does the change solve the Issue?
- [ ] Are Acceptance Criteria satisfied?
- [ ] Is the code readable?
- [ ] Are comments left?
- [ ] Is production data protected?
- [ ] Are secrets excluded?
- [ ] Are hooks, fixtures, or migrations documented if required?
- [ ] Is the testing evidence sufficient?
```

### 18.2 Developer Responsibilities

Developer should:

```md
- [ ] Read every review comment.
- [ ] Update code in the same feature branch.
- [ ] Push new commits.
- [ ] Reply to each comment.
- [ ] Mark conversations as resolved only after the reviewer concern is addressed.
- [ ] Request re-review when updates are complete.
```

Do not open a second PR for the same review comments unless the requested change is intentionally split into a separate Issue.

---

## 19. How to Review PR Comments in VS Code

Use the GitHub Pull Requests and Issues extension.

### Initial Setup

1. Open VS Code.
2. Install extension: **GitHub Pull Requests and Issues**.
3. Sign in to GitHub from VS Code.
4. Open the local repo folder.
5. Make sure the branch is connected to the same GitHub repository.

### Open PR in VS Code

1. Open the GitHub Pull Requests panel on the left sidebar.
2. Find the Pull Request.
3. Click the PR.
4. Open **Description**, **Changes**, or **Comments**.
5. Click files under the PR to see inline comments.

### If Comments Do Not Appear

Check:

```text
- You are signed into the correct GitHub account.
- VS Code opened the correct repo folder, not a parent folder or old clone.
- The local repo remote points to the GitHub org repo.
- The PR is from the same repo/branch you opened.
- The extension is enabled.
- You selected the PR under the GitHub Pull Requests panel.
- You opened the PR diff view, not just the local file.
```

Useful commands:

```bash
git remote -v
git branch
git status
```

The remote should point to the SpecwinOrchard org repo, for example:

```text
git@github.com:Spectracell-SpecwinOrchard/specwinorchard2026.git
```

---

## 20. Updating Files During Code Review in VS Code

When reviewer comments require code changes:

```bash
git status
git branch
```

Confirm you are on the PR branch:

```bash
git checkout feature/12-po-email-recipients
```

Make edits in VS Code.

Then:

```bash
git status
git diff
git add .
git commit -m "fix: address audit log service review comments"
git push
```

After push:

1. Go back to the PR.
2. Reply to the review comment.
3. Explain what changed.
4. Resolve the conversation only when appropriate.
5. Request re-review.

Do not create a new branch unless the reviewer explicitly asks to split the work.

---

## 21. Replying to Code Review Comments

Use clear replies.

Good examples:

```text
Updated in commit 4f3a2c1. The audit write now uses a post-commit hook so it does not block the validation pipeline response.
```

```text
Good catch. I extracted the audit write into a dedicated AuditService class and added comments explaining the graceful degradation behaviour on write failure.
```

```text
I did not change this part because it belongs to a separate Issue. I created #18 to track it.
```

unless the change is very small and obvious.

---

## 22. When to Create a Sub-Issue vs New Issue

Use this decision rule.

### Convert to Sub-Issue When

The work is directly required to complete the original Issue.

Example:

```text
Original Issue: Audit log for validation runs
Sub-Issue: Add AuditService helper class for reusable write operations
```

### Create a New Issue When

The work is related but not required for the original Issue.

Example:

```text
Original Issue: Audit log for validation runs
New Issue: Add audit log export to CSV for CAP inspector reporting
```

### For the Current Audit Log Case

If the original Issue says both validation run events and login events must be recorded, then a missing login event should remain part of the same Issue or become a sub-issue under it.

If the original Issue only covered validation run audit and login tracking was discovered later as a new requirement, create a new Issue.

Recommended approach:

```text
Keep it under the same Issue if Acceptance Criteria already mention login events.
Create a new Issue if login tracking was not part of the original Acceptance Criteria.
```

---

## 23. Pull Request Template File

Create:

```text
.github/pull_request_template.md
```

Content:

```md
## Summary

## Linked Issue

Closes #
```

---

## 24. GitHub Workflow Rules for SpecwinOrchard

Use these rules as operating policy:

1. No direct commit to `main`. All branches will link to `develop` as the destination.
2. No production deployment from unreviewed code.
3. Every business-facing change needs an Issue.
4. Every code change needs a PR.
5. Every PR must link its Issue.
6. Every PR must include testing notes.
7. Production scripts require backup and dry-run first.
8. Vendor data, Excel imports, credentials, `.pem`, `.env`, and database backups must not be committed.
9. Done means merged, reviewed, tested, documented, and closed.
