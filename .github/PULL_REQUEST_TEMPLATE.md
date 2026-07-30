<!--
Thanks for contributing to Dusky.

Small PRs get merged fast. If this is a work in progress, open it as a draft.
Docs and typo fixes: feel free to delete any section below that does not apply.
-->

## What does this change?

<!-- One or two sentences. What was wrong or missing, and what does this do about it? -->

## Why?

<!-- Link an issue if there is one: "Fixes #123". Otherwise, briefly: what problem does this solve? -->

## Type of change

- [ ] Bug fix
- [ ] New feature / script
- [ ] New setup subscript (`arch_setup_scripts/scripts/`)
- [ ] Theming / visual change
- [ ] Documentation
- [ ] Refactor or cleanup (no behaviour change)

---

## Tested on

<!--
This is the most useful section of the whole template. Dusky breaks along hardware lines
more than anything else, so "works for me" without context is hard to act on.
-->

- **GPU:** <!-- NVIDIA proprietary / AMD / Intel / hybrid Intel+NVIDIA / VM -->
- **Machine:** <!-- laptop / desktop / VM -->
- **Filesystem:** <!-- BTRFS / ext4 -->
- **Dusky version:** <!-- output of: cat ~/.config/dusky/version -->

**How I tested it:**

<!-- e.g. "Ran the script fresh, then re-ran it to confirm idempotency. Rebooted and confirmed the service comes up." -->

## Checklist

- [ ] I read [CONTRIBUTING.md](.github/CONTRIBUTING.md)
- [ ] Shell scripts pass `bash -n` and `shellcheck`
- [ ] Python files pass `python -m py_compile`
- [ ] Scripts start with `#!/usr/bin/env bash` and `set -euo pipefail`
- [ ] **The script is safe to run twice** — I actually ran it twice
- [ ] No hardcoded `/home/<username>` paths (`$HOME` used instead)
- [ ] No generated files committed (see `.gitignore`)
- [ ] No personal data committed — SSH keys, auth keys, hostnames, drive UUIDs
- [ ] If I added a config file under `.config/`, I also added it to `.git_dusky_list`
- [ ] If I added a setup subscript, it is numbered correctly and fails soft

## Screenshots / recording

<!--
Required for anything visual — this is a ricing project and reviewers need to see it.
Drag images straight into this box. Before/after side by side is ideal.
-->

## Anything reviewers should know

<!-- Known limitations, hardware you could not test on, follow-up work you plan to do. -->
