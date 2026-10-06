# Creating a Beta Branch for Dusky

## Purpose

Keep `main` as the public update branch while developing changes that need
unreleased packages on `future`. Commit and push work regularly on `future`.
When the release is ready, merge `future` into `main` without squashing so every
development commit remains in the history users see on `main`.

For the Hyprland 0.57 development cycle, the intended policy is:

- Work exclusively on `future`; leave `main` unchanged until release.
- Keep GitHub's default branch and the shipped updater profiles on `main`.
- Announce the required system update on Discord, wait a few days, then merge.
- The developer does not run the Dusky updater to synchronize development work.

## Understand this repository

Dusky is a **bare repository** stored at `$HOME/dusky`. Its live work tree is
`$HOME`, including the configuration files your desktop actually uses.

The `git_dusky` function in `$HOME/.config/zshrc/git` is equivalent to:

```zsh
/usr/bin/git --git-dir="$HOME/dusky" --work-tree="$HOME" COMMAND
```

Use `git_dusky` for these operations, rather than plain `git` from your home
directory. In a fresh Zsh session where the functions are unavailable:

```zsh
source "$HOME/.config/zshrc/git"
```

A branch is a name pointing to a commit. Creating `future` from `main` starts
both names at the same commit; subsequent commits advance only the active
branch. Uncommitted changes are work-tree/index state, not permanently attached
to a branch. **Creating a branch does not save those changes; committing does.**

If you ran bare `gitdelta` while on `main`, it may already have staged your
changes. That does not put them into `main`'s committed history. Creating
`future` at the same starting commit carries the staged changes across; a
subsequent commit on `future` saves them there, leaving `main` unchanged.

## Create and publish a future branch

First inspect the current branch, pending changes, and upstream:

```zsh
git_dusky branch -vv
git_dusky status --short
git_dusky diff --stat HEAD
git_dusky diff --cached --stat
```

This repository hides untracked files in ordinary status. To check new files in
the directories you are working on, specify those directories explicitly:

```zsh
git_dusky status --short --untracked-files=all -- .config/hypr user_scripts/hypr
```

Save an independent backup of important pending files before the transition.
Then, while on `main`, create `future` at the current commit:

```zsh
git_dusky switch --no-track -c future
git_dusky branch --show-current
git_dusky status --short
```

The starting commit is unchanged, so pending changes can carry across.
`--no-track` prevents the new branch from inheriting an upstream such as
`origin/main`. Do not use `-C` to recreate an existing branch: that can reset its
pointer. If `future` already exists, use `git_dusky switch future` instead,
after checking that switching will not replace live configs you need.

If the intended changes are already staged, commit them directly. Otherwise
stage explicit paths, or use `git_dusky_add_list` to stage manifest files and
all tracked modifications/deletions, then review what was staged:

```zsh
git_dusky_add_list
git_dusky diff --cached --stat
git_dusky diff --cached
git_dusky commit -m "Prepare configuration for the next release"
git_dusky push -u origin future
git_dusky branch -vv
```

The last command should show `future` tracking `origin/future`. The push creates
the branch on GitHub; it does not merge it or change GitHub's default branch.
People updating from `main` do not receive commits exclusive to `future`.

## Daily development

**Both your existing `dusky` script and your usual command chain work unchanged.**
They use the active branch; neither automatically switches to `main`. With
`future` active and tracking `origin/future`, commits go onto `future` and
ordinary pushes go to GitHub's `future` branch.

Check the active branch and upstream:

```zsh
git_dusky branch -vv
```

Look for `* future` and `[origin/future]`. The `*` marks the active branch.
As long as that configuration remains in place, use your familiar command:

```zsh
git_dusky_add_list && git_dusky commit -m "commit text description" && git_dusky push
```

The `&&` chain runs each next command only if the preceding command succeeds.
If there is nothing to commit, the commit command fails and the push is
skipped. To publish previously created local commits, run `git_dusky push`
separately or use `dusky 4`.

For a step-by-step workflow with a staging review and an explicit push target:

```zsh
git_dusky branch --show-current
git_dusky_add_list
git_dusky diff --cached --stat
git_dusky commit -m "Describe the change"
git_dusky push origin future
```

Create commits as often as useful. Each commit is a saved snapshot; pushing
backs up those commits on GitHub. A push does not save uncommitted edits.

Your existing manager also works with this branch:

| Command | Behavior |
|---|---|
| `dusky 1` | Commit manifest-scoped changes, then offer to push to the configured upstream |
| `dusky 2` | Select files to commit, then offer to push |
| `dusky 3` | Commit locally without pushing |
| `dusky 4` | Push existing local commits to the configured upstream |
| `dusky 7` | Open branch creation/switch/merge/push tools |
| `git_dusky_push FILE` | Commit that file and immediately push the active branch |

Check the active branch and upstream before using commit/push helpers.
The script's `safe_push()` reads the active branch's configured upstream;
for this setup its destination is `origin`, `refs/heads/future`. `dusky 3`
still leaves the new commit local until you push it later. If you deliberately
switch to another branch, both workflows will commit on that branch instead.

To confirm the destination without sending any updates:

```zsh
git_dusky push --dry-run --porcelain
```

For this setup, the output should show
`refs/heads/future:refs/heads/future`. This checks the push destination, without
staging or committing files.

`dusky_backup_manager.py --new` recreates repository metadata; `--relink` can
reset the index, reconcile history, prune tracking according to the manifest,
commit, and push. Neither is needed for ordinary branch development.

### Viewing changes without staging

Bare `gitdelta` and `gitdelta select` stage manifest entries and tracked changes
before displaying them. For a read-only view, use:

```zsh
git_dusky diff HEAD
git_dusky diff --cached
gitdelta .config/hypr/source/appearance.lua
```

`gitdelta FILE...` does not perform the manifest staging step. To render every
currently changed tracked/staged path without staging anything, in Zsh:

```zsh
changed_files=( "${(@0)$(git_dusky diff --name-only -z HEAD)}" )
if (( $#changed_files )); then
    gitdelta "${changed_files[@]}"
fi
```

Untracked new files still require separate inspection.

### GitHub notices after pushing future

“future had recent pushes” and **Compare & pull request** offer to open a pull
request. They do not merge anything automatically; you can leave the offer
alone until release. “Your main branch isn't protected” suggests optional
branch rules. It does not mean the branch is damaged or that your changes
were committed to `main`; protection is not required for this workflow.

## Merge the release into main, preserving every commit

When release day arrives, finish and commit development work first. Confirm
that `future` is active and tracked files are clean; inspect any untracked new
files in the relevant directories too. Run the release checks you intend for
the changed configuration and tools, then publish the final future commits.

```zsh
git_dusky branch --show-current
git_dusky status --short
git_dusky push origin future
git_dusky fetch origin
git_dusky log --oneline main..future
git_dusky diff --stat main..future
```

After the announcement and waiting period, fetch again so the remote branch
references are current, then release with:

```zsh
git_dusky fetch origin
git_dusky switch main
git_dusky merge --ff-only origin/main
git_dusky merge --ff-only future
git_dusky push origin main
```

Run commands one at a time and stop if any fails. The first merge ensures local
`main` includes the fetched public tip. The second advances `main` to `future`
when its history is a direct continuation of `main`, as intended when `main`
has no intervening work. `--ff-only` refuses an unexpected history divergence;
it does not create a merge commit or rewrite existing commits.

**Switching to `main` changes your live dotfiles to its contents.** Switching
and then merging are separate steps, so the older files are briefly active
between them. Do this at a suitable time; avoid restarting the session during
that interval.

The history changes like this:

```text
Before release:
A                  main
 \
  B---C---D        future

After the fast-forward:
A---B---C---D       main, future
```

Commits `B`, `C`, and `D` keep their original IDs, messages, authors, and
timestamps. The original baseline `A` remains in history. There is no new
massive combined commit. The first commit of this cycle saves the previously
uncommitted batch together; every later commit is preserved individually too.

Do not use `merge --squash` or GitHub's **Squash and merge** for this workflow.
If merging through GitHub, **Create a merge commit** retains the development
commits and adds a merge commit; the terminal fast-forward above retains them
without adding one. Avoid **Rebase and merge** if keeping original commit IDs
is important.

Verify the release:

```zsh
git_dusky branch -vv
git_dusky log --oneline --decorate -15 main
git_dusky rev-parse main future
git_dusky ls-remote origin refs/heads/main refs/heads/future
```

Immediately after the intended fast-forward, `main` and `future` should resolve
to the same commit, and GitHub should show that commit for both branches. Keep
`future` for the next development cycle; deleting it is unnecessary. When
starting that cycle, switch back to `future` before committing new work.

```zsh
git_dusky switch future
git_dusky branch -vv
```

The usual `dusky` commands and command chain then continue publishing to
`origin/future` for the next cycle. Keep the two branches; there is no need to
delete or recreate `future` after a release.

## Updater and installation branch selection

The shipped default updater profile explicitly selects `main`, independently
of your active development branch. Leave it that way. The README's ordinary
clone follows GitHub's default branch, which also stays `main`.

The developer's workflow uses Git directly. Running the default updater while
on `future` is not a way to bring in `main` fixes. Its
`--allow-diverged-reset` option can reset the active branch to the configured
upstream. Do not use it for this branch workflow.

## Recorded transition: 2026-10-06

- Stable `main` baseline: `522cb09c4a1a578c3e1fd98b377bd8ad44371fdb`.
- Created `future` from that baseline with no inherited upstream.
- Initial future commit: `e8b42f13`, “Prepare Hyprland 0.57 configuration and TUI controls”.
- That commit contains the 16 previously staged files: 2,798 insertions and 600 deletions.
- This reference note is committed separately on `future`.
- Pre-transition backup directory:
  `$HOME/Documents/dusky_git_backups/20261006_150559_before_future/`.
- `repository.bundle` contains pre-transition committed refs/history and passed
  `git bundle verify`. `pending_changes.patch` contains the pending binary-aware
  diff against `HEAD`; `index` preserves the original staging data, and
  `state.json` records the main baseline and pending-file hashes.
- The bundle alone does not include the previously uncommitted changes; retain
  the patch alongside it. The saved index is recovery evidence, not a file to
  copy blindly over a newer repository index.

## Git references

- [Creating and switching branches](https://git-scm.com/docs/git-switch)
- [Merging and fast-forward behavior](https://git-scm.com/docs/git-merge)
- [Pushing branches and setting upstreams](https://git-scm.com/docs/git-push)
- [Git bundles for history backups](https://git-scm.com/docs/git-bundle)
