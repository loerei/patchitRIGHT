# Detailed Git Workflow Protocols

Step-by-step operational safeguards for Git state-modifying work.

## Pre-Task: Fresh State

Before starting state-modifying work or creating new commits:
1. Run `git fetch origin` and `git rebase origin/<default-branch>` (or target branch).
2. **MUST NOT** create merge commits (`Merge branch 'main' into ...`).
3. If the rebase produces conflicts, resolve them locally before proceeding — or abort the rebase (`git rebase --abort`) and consult the user if conflicts are non-trivial.

---

## Branch Operations: Stash Gate

Before running `git checkout`, `git switch`, or `git rebase`:
1. Check `git status` for uncommitted changes.
2. If dirty working tree exists, **MUST** `git stash` or commit changes first.
3. **NEVER** run checkout/switch/rebase over uncommitted changes.

---

## Committing: Atomic & Conventional

1. Each commit **MUST** solve exactly one logical change.
2. Commit messages **MUST** follow **Conventional Commits** format in English:
   - `feat:` New feature
   - `fix:` Bug fix
   - `refactor:` Code refactoring without behavior change
   - `test:` Adding or updating tests
   - `docs:` Documentation updates
   - `style:` Formatting/style adjustments

---

## Pre-Push: Re-fetch & Verification

Before pushing to remote:
1. Run `git fetch origin` and `git rebase origin/<default-branch>` again to pick up upstream changes.
2. Resolve all merge/rebase conflicts locally.
3. Verify that all automated tests and build checks pass clean before pushing or opening PRs.
