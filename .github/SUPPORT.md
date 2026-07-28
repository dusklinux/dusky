# Getting help with Dusky

Start with the fastest route for your situation.

## 💬 Discord — start here

**[discord.gg/Nv2a7yTBQS](https://discord.gg/Nv2a7yTBQS)**

Most Dusky problems are hardware-specific and get solved in minutes by someone who has hit
the same thing. Installation trouble, "how do I change X", GPU quirks, and general
questions all belong here rather than in an issue.

## 📺 Video walkthroughs

- [Installation tutorial](https://youtu.be/OzeFAY_8T8Y)
- [Feature demo](https://youtu.be/JmgvSdEIK8c)

## 📚 The included documentation

Dusky ships a substantial Obsidian vault at `~/Documents/pensive/`. It covers Arch
installation, BTRFS and snapshots, GPU passthrough, KVM, NVIDIA, TLP and power tuning,
disk management, and networking. If your question is "how does this Linux thing work", the
answer is quite possibly already written down there.

## ⌨️ The built-in cheatsheet

Press `CTRL` + `SHIFT` + `SPACE` for the keybind cheatsheet. Entries are clickable and run
the command directly.

## 🔧 Self-service troubleshooting

Two steps fix most breakage:

```bash
# 1. Re-run the orchestrator — it is idempotent and safe to re-run.
~/user_scripts/arch_setup_scripts/ORCHESTRA.sh

# 2. If a specific subscript failed, run just that one to get a clearer error.
~/user_scripts/arch_setup_scripts/scripts/<the_failing_script>.sh
```

The scripts are modular — one failure does not mean a broken system, and the rest of the
install generally completes fine.

## 🐞 Opening an issue

If you have a **reproducible defect** rather than a support question, open a
[bug report](https://github.com/dusklinux/dusky/issues/new?template=bug_report.yml).
Please include GPU vendor, filesystem, and install method — those three fields resolve
most reports.

## 🔒 Security issues

Do not report these publicly. See [SECURITY.md](SECURITY.md).
