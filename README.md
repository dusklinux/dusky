# dusker
a fork of [dusky](https://github.com/dusklinux/dusky) by dusklinux. tweaks, addons, and personal changes by [veltraced](https://github.com/veltraced).

> nothing is pushed yet. this is a work in progress.

---

## what's coming

- [ ] performance optimizations and system tweaks
- [ ] full vm setup — spin up any iso quickly with minimal effort
- [ ] gaming mode script bundled in and ready to go (already made)
- [ ] other stuff as i go

---

## install

> based on dusky's install. requires a fresh arch install with hyprland and btrfs.

```bash
sudo pacman -Syu --needed git
```

```bash
git clone --bare --depth 1 https://github.com/veltraced/dusker.git $HOME/dusker
```

```bash
git --git-dir=$HOME/dusker/ --work-tree=$HOME checkout -f
```

then run the setup script:

```bash
~/user_scripts/arch_setup_scripts/ORCHESTRA.sh
```

takes 30–60 mins. don't leave it unattended, you'll get a few prompts.

---

## original project

all credit for the base goes to [dusklinux/dusky](https://github.com/dusklinux/dusky). go star it.
