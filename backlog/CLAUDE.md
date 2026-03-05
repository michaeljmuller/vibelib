# Backlog.md Usage

**Never edit task files directly. All changes must go through the CLI.**

Run `backlog` commands from the repo root.

## Workflow

```bash
# Claim a task
backlog task edit 42 -s "In Progress" -a @claude

# Add implementation plan, share with user, wait for approval before coding
backlog task edit 42 --plan $'1. Do X\n2. Do Y'

# Check off ACs as you complete them
backlog task edit 42 --check-ac 1 --check-ac 2

# Add a final summary and move to Testing (do NOT mark Done or archive)
backlog task edit 42 --final-summary "What changed and why"
backlog task edit 42 -s "Testing"
```

## Common Commands

```bash
# Reading
backlog task list --plain
backlog task list -s "To Do" --plain
backlog task 42 --plain
backlog search "topic" --plain

# Creating
backlog task create "Title" -d "Description" --ac "Criterion 1" --ac "Criterion 2"

# Editing
backlog task edit 42 -t "New title"
backlog task edit 42 -d "New description"
backlog task edit 42 -s "In Progress"
backlog task edit 42 -a @claude
backlog task edit 42 -l bootstrap,api

# Acceptance criteria
backlog task edit 42 --ac "New criterion"
backlog task edit 42 --check-ac 1 --check-ac 2
backlog task edit 42 --uncheck-ac 1
backlog task edit 42 --remove-ac 3

# Notes and summary
backlog task edit 42 --append-notes $'- Did X\n- Did Y'
backlog task edit 42 --final-summary "PR-style description"
```

## Multi-line Input

Use ANSI-C quoting for real newlines — `"...\n..."` passes a literal backslash-n:

```bash
backlog task edit 42 --plan $'1. Step one\n2. Step two'
backlog task edit 42 --append-notes $'- Investigated root cause\n- Fixed the bug'
```

## Project Rules

- Statuses: To Do → In Progress → Testing → Done
- Move tasks to **Testing** when complete; do not mark Done or archive
- Only implement what's in the Acceptance Criteria
- Use `--plain` for all read operations
