# dusker
a fork of [dusky](https://github.com/dusklinux/dusky) by dusklinux. tweaks, addons, and personal changes by [veltraced](https://github.com/veltraced).

---

## what's here

- claude.ai matugen theme
- spotify/spicetify matugen theme fix
- matugen tweaks addon (one-click claude + spotify)
- gaming mode script (`~/user_scripts/performance/gaming_mode.sh`)
- full vm setup (kvm + storage + vfio)
- input remapper pre-configured
- dusker control center with horizontal layout
- performance tweaks and system optimizations

---

## merging upstream

to pull latest changes from the original project without losing your customizations:

```bash
~/user_scripts/dusky_system/merge_upstream/merge_upstream.sh
```

or use the **Add-ons → Merge Upstream Changes** button in Dusker.

---

## install

> based on dusky's install. requires a fresh arch install with hyprland and btrfs.

```bash
sudo pacman -Syu --needed git
```

```bash
git clone --bare --depth 1 https://github.com/veltraced/dusker.git $HOME/dusky
```

```bash
git --git-dir=$HOME/dusky/ --work-tree=$HOME checkout -f
```

then run the setup script:

```bash
~/user_scripts/arch_setup_scripts/ORCHESTRA.sh
```

takes 30–60 mins. don't leave it unattended, you'll get a few prompts.

---

## original project

all credit for the base goes to [dusklinux/dusky](https://github.com/dusklinux/dusky). go star it.
