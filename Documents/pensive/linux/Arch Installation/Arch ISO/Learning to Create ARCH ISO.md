# Part 1 of 3 — Corrected architecture and mental model for a fully offline custom Arch ISO

> **Scope and intent**
>
> This guide is written for a **modern Arch Linux workflow** and assumes a **UEFI-based target system**. It intentionally avoids legacy/BIOS-focused design.
>
> It is also written to be **architecture-first** rather than command-first. That means it explains the system in a way that should remain useful even when some implementation details change over time.
>
> Where Arch-specific behavior can vary by release or tooling revision—especially things like the exact live-image format used by `archiso`, the exact boot artifact layout, or current best-practice bootloader details—this guide uses **format-neutral language unless a detail is stable enough to state generally**.
>
> In other words: this is meant to be **accurate and durable**, not brittle.

---

## The one big idea

A fully offline custom Arch installer ISO is **not one thing**.

It is really **three distinct systems and one build pipeline**:

1. **The Factory**  
   The build machine or build environment where you assemble everything.  
   This is the only place that is allowed to use the internet.

2. **The live installer environment**  
   The temporary operating system that boots from the ISO/USB and runs your installer logic.

3. **The offline package source**  
   A local `pacman` repository stored on the ISO that contains every package needed for installation, plus the repository metadata needed for dependency resolution.

4. **The target installed system**  
   The final Arch Linux installation written to the user’s disk.

If you keep those four roles separate in your head, the whole design becomes understandable.

If you mix them together, everything becomes confusing.

---

## The second big idea

An offline Arch install is **still an Arch install**.

Nothing about “offline” changes what installation fundamentally is.

At first principles, an Arch installation is simply:

- partition disks
- create filesystems
- mount them
- install a Linux userspace into the target root
- install kernel and boot-related artifacts for the target system
- write machine configuration
- create users
- make the installed disk bootable

That is all.

`pacstrap` is only a convenience layer around `pacman` for installing packages into a mounted target root.

So when you go offline, you are **not changing what installation is**.

You are only changing **where `pacman` gets package metadata and package files from**.

### Online install
- metadata comes from remote repos
- package files come from remote repos

### Offline install
- metadata comes from a local repo on the ISO/USB
- package files come from that same local repo

Dependency solving is still `pacman`.
Package extraction is still `pacman`.
The target package database is still populated normally.

The internet is simply replaced by a **local transport path**.

---

# 1. The master architecture

Before anything else, here is the full conceptual picture.

Your project has at least these layers:

## Layer A — The Factory source and build environment
This is your controlled build world.

It contains:
- your source notes and scripts
- your dotfiles repo
- your custom package recipes
- `archiso`
- package download/cache logic
- AUR build logic
- release manifests/checksums if you generate them

This is the only layer that should touch the internet.

---

## Layer B — The ISO image as a distribution artifact
This is the `.iso` file you ultimately ship.

Conceptually it contains:
- boot structures and boot assets for the installer medium
- a live root image for the installer environment
- a local package repository for offline installation
- optional release metadata and checksums

This is a transport artifact, not itself the final installed OS.

---

## Layer C — The booted live installer environment
When a user boots the ISO, they are running a temporary operating system.

That live environment contains:
- shell and core userspace
- `pacman` / `pacstrap`
- partitioning and filesystem tools
- your installer scripts
- trust/keyring state needed for offline verification
- optionally a graphical live session, if you choose that design

Its job is to **install** Arch, not to *be* the final Arch system.

---

## Layer D — The local offline repository on the ISO
This is the package warehouse used by `pacman`/`pacstrap`.

It contains:
- official Arch package archives
- your prebuilt AUR packages
- your own custom packages
- repository metadata
- optional signatures, depending on your trust model

This is one of the most important layers in the whole project.

Without it, there is no clean offline installation.

---

## Layer E — The target installed system
This is the real system written to the user’s disk.

Inside it, the most important conceptual zones are:

- **`/etc`** — machine-wide policy and configuration
- **`/usr`** — package-managed software and shared resources
- **`/usr/share`** — shared static data/resources
- **`/etc/skel`** — default home template for future users
- **`/home/username`** — the actual created user’s real home
- **`/var`** — mutable system state, package database, logs, caches, service state

---

# 2. What `archiso` actually is

`archiso` is Arch’s official toolkit for building bootable live media.

That description is correct, but incomplete.

Conceptually, `archiso` has two major roles.

## Build-side role
It constructs a live Linux filesystem tree from:
- packages
- your custom files
- profile configuration
- optional build-time customization hooks

## Runtime role
It provides the logic and conventions needed for:
- booting a live environment from installation media
- locating the live medium
- mounting a read-only live root image
- making that environment usable as a running system

So `archiso` is not just “a tool that outputs an ISO.”

It is a framework for:
- building a live root
- packaging it into bootable installation media
- supporting the runtime mechanics of that live environment

---

## 2.1 What `airootfs` really is

This is one of the most commonly misunderstood parts.

`airootfs` is **not** the whole live operating system by itself.

It is better understood as:

- a filesystem overlay tree
- a set of files you want copied into the staged live root during ISO construction

The live root is generally created by:
1. installing packages into a staging directory
2. copying your `airootfs` files into that staging root
3. applying any other build-time customizations
4. packaging the result into the live image used by the booted installer

So the live installer environment comes from **two broad sources**:

### Source 1 — packages
These create most of the live OS:
- shell
- system tools
- package manager
- editors/pagers
- hardware utilities
- partitioning tools
- anything else the live environment needs

### Source 2 — your custom overlay files
These are things like:
- installer scripts
- custom live-session configs
- helper scripts
- systemd unit files for the live session
- branding assets
- MOTD
- local docs
- custom pacman config for the live environment
- trust material or repo config needed in the live installer

That is what `airootfs` is for.

---

## 2.2 The staged live root is not the target system

This distinction is absolutely critical.

The live root built by `archiso` is **not** the same thing as the system installed onto the user’s disk.

It is only the installer operating system.

That means it may contain things that should **never** define the final installed system, such as:
- live-media boot assumptions
- temporary runtime behavior
- installer-only tools
- debugging utilities
- live-session convenience settings
- demo/live-session user config
- scripts that exist only to perform installation

A correct Arch installation normally builds the target system by **installing packages into the target root**, not by cloning the live root wholesale.

That is one of the central design principles of your project.

---

# 3. The build machine and runtime machine are not the same package-manager context

There are at least **three distinct `pacman` contexts** in this kind of project, and it is important not to mix them up.

## Context 1 — Factory build-time package access
This is used while building the ISO itself.

This context may:
- access the internet
- download official packages
- populate the live build root
- install build dependencies
- build AUR packages
- create the offline local repository

This is a build concern.

---

## Context 2 — Live installer runtime package access
This is the `pacman` context inside the booted ISO.

This context must be able to:
- read the local repo from the ISO/USB
- verify packages offline
- run `pacstrap` using only local media
- never depend on internet access

This is the installation concern.

---

## Context 3 — Target installed system package policy
This is the `pacman` configuration of the final installed Arch system.

This is a separate design decision.

You must decide what happens **after installation**:

- Will the installed system use normal online Arch repos?
- Will it also reference your own custom repo?
- If yes, where will that repo exist after install?
- If no, are your custom packages install-time-only artifacts?

This must be designed deliberately.

A common mistake is to get the ISO installer working offline, but leave the installed system pointing at a dead `file://` source that only existed on the installer medium.

Do not do that.

---

# 4. What a local `pacman` repository actually is

A local repository is not “a fake website.”

It is simply a normal `pacman` repository stored on local media instead of served over the network.

At first principles, a `pacman` repository consists of:

- package archives
- repository metadata describing those packages

That is all.

`pacman` fundamentally does not care whether that repository is reached via:

- HTTP
- another network protocol
- or a local file path

It only cares that it can:
- read the repository metadata
- locate package files
- verify packages according to policy
- install them

That is why offline installation works so well when designed correctly.

You are not tricking `pacman`.
You are giving it the same kind of repository it expects online, but through a local path.

---

## 4.1 What is inside an Arch package

An Arch package archive contains the actual installable payload.

Conceptually it includes:
- files that will be installed onto the target filesystem
- package metadata such as:
  - name
  - version
  - architecture
  - dependencies
  - conflicts/provides
  - description
- optional install scriptlets
- package metadata used by package tools

The important point is this:

An Arch package is **not** an instruction to later download software.

It *is* the software payload.

That is why offline installation is possible.

If the package archive is present locally, `pacman` can install it without the network.

---

## 4.2 Package cache vs repository

These are not the same thing.

### A package cache
A package cache is just a collection of package files that happened to be downloaded.

### A repository
A repository is:
- package files
- plus metadata that indexes them as installable packages

Why this matters:

For a distributable offline installer, you generally want a **real local repository**, not just a random folder of cached package files, because a real repo gives you:

- dependency resolution
- normal package-name-based installation
- version-aware selection
- clean `pacstrap` behavior
- much less ad hoc scripting

For your use case, the repository model is the right architecture.

---

# 5. Official packages, AUR packages, and your own packages

Your offline ISO will likely contain three different package classes.

## Class 1 — Official Arch packages
These are packages from the official Arch repos.

Examples:
- base system
- kernel
- firmware
- bootloader packages
- networking packages
- graphics stack
- compositor and desktop tools
- audio stack
- applications
- fonts
- themes
- utilities

These remain official Arch packages, even when you redistribute them inside your own local repo.

---

## Class 2 — Prebuilt AUR packages
The AUR is **not** a binary package repository.

It is a collection of build recipes.

That means if you want AUR software to be installable from your offline ISO, you must turn those recipes into normal binary packages **in the Factory** first.

At install time, those are no longer “AUR installs.”

They are just your prebuilt packages in your local repo.

That distinction matters a lot.

---

## Class 3 — Your own custom packages
These are packages you create yourself specifically to carry your own payloads.

This is one of the cleanest and most powerful design tools you have.

Excellent examples include:
- a package that installs your target `/etc/skel`
- a package that installs wallpapers/themes/icons
- a package that installs machine-wide defaults into `/etc`
- a package that installs branding assets
- a package that installs user workflow scripts into a system path
- a package that installs XDG defaults or session descriptors

Custom packages are often cleaner than endless post-copy scripting because they are:
- versioned
- reproducible
- trackable
- easier to update later
- naturally handled by `pacstrap`

---

# 6. Snapshot consistency in a rolling distribution

This is one of the most important architectural requirements in Arch.

Arch is a rolling-release distribution.
That means packages are meant to work together as part of a **moving repository state**.

So when you build an offline ISO, you should think in terms of a **coherent snapshot**, not in terms of “whatever I happened to download over time.”

Your snapshot must include, as one coherent release set:

- official package versions
- prebuilt AUR package outputs
- your custom package outputs
- repository metadata
- trust/keyring state needed to verify those packages
- your dotfiles/user-defaults payload
- your installer logic

If you build these pieces at unrelated times, you risk creating a mixed universe where:
- one package expects one library version
- another was built against a different library version
- an AUR package was built against an older repo state
- the keyring is older than the package snapshot
- your ISO installs inconsistently or breaks later

For Arch, “offline” must be built around the idea of **one release snapshot**.

---

## 6.1 Why AUR clean builds matter

When you build AUR packages for redistribution, build them in a clean, controlled environment.

Why?

Because on a dirty host, a package may successfully build only because your host already had undeclared dependencies installed.

That creates the classic “works on my machine” failure.

A clean build environment helps ensure:
- dependencies are explicit
- package output is fit for redistribution
- you are not accidentally leaking host state into the result

For your use case, this is not optional as a quality standard.
If you are distributing an ISO to other people, you are acting as a small distribution maintainer.

---

## 6.2 Why VCS AUR packages are risky

Version-control-source packages such as `-git` variants are often built from moving upstream source states.

That means:
- reproducibility is worse
- your output can change even if you did not mean it to
- breakage risk rises
- debugging becomes harder
- redistributable snapshots become less stable

For a public offline ISO, fixed-release packages are generally safer than moving VCS packages.

If you do ship VCS-built packages, then the exact source revision effectively becomes part of your release snapshot and should be treated as such.

---

# 7. Trust, signatures, and keyrings in an offline ISO

Offline installation does **not** remove trust requirements.

It only removes network access.

If your package verification policy is enabled—and in a serious installer it generally should be—you need to plan trust very carefully.

---

## 7.1 There are multiple trust layers

At least three trust relationships exist in this project.

### Layer 1 — Your Factory trusts upstream
Your build environment decides what to trust from:
- official Arch repos
- the Arch keyring
- AUR build recipes
- upstream source tarballs
- your own source trees/assets

### Layer 2 — The live installer trusts the packages on the ISO
The booted installer must be able to verify packages **offline**.

That means the live environment must contain the trust state needed to verify what it is installing.

### Layer 3 — Your users trust your ISO
Once you distribute the ISO, users are trusting:
- the package set you assembled
- the AUR packages you built
- the custom packages you created
- the trust policy you embedded
- the release artifact itself

If you distribute a custom Arch ISO, you are effectively taking on a small-distribution maintainer role.

---

## 7.2 Official package verification offline

Official Arch packages can still be verified offline, **provided the necessary trust material is already present locally**.

That generally means your live installer environment must be prepared with:
- a sufficiently current Arch keyring state
- the trust database/keyring content needed for verification
- any relevant signature material required by your verification policy

The exact implementation details can vary, but the architectural point is stable:

**the live environment must be able to verify the packages it installs without contacting a keyserver or the internet.**

---

## 7.3 Keyring freshness is part of the release snapshot

This deserves its own section because it is so important.

A stale keyring can break an otherwise-correct offline installer.

So your release snapshot is not just:
- package files
- repo metadata

It is also:
- keyring/trust state current enough for those package files

For a rolling-release distro like Arch, keyring freshness is an installability issue, not only a security issue.

Treat the keyring as part of the release artifact.

---

## 7.4 Your custom packages and repo trust

For your own custom packages—or AUR packages you built and now redistribute as binaries—you have two broad trust models.

### Model A — Stronger model
You sign your custom packages and/or repository metadata with your own key and ship the corresponding trust setup.

This is the cleaner distribution model.

### Model B — Simpler but weaker model
You choose a more relaxed trust policy for your own custom repo artifacts.

That may be easier to prototype, but it is weaker and should be treated as such.

If the **installed target system** will continue to use your custom repo after installation, then target-side trust setup is **not optional**.  
It must be planned and configured.

If your custom repo exists only on the installer medium and is never used again after installation, then target trust for that repo may not be needed later.

That distinction matters.

---

## 7.5 Package signatures vs repository signatures

Do not conflate these.

They are related but not identical.

Conceptually, you may be dealing with:
- signatures on package artifacts
- signatures on repository metadata
- trust policy controlling how `pacman` evaluates both

If you build your own local repo database, that repository metadata is **your artifact**, even if many packages inside it are official Arch packages.

That means the trust model for “official package signatures” and the trust model for “your local repo metadata” are not automatically the same thing.

This is an easy area to misunderstand, so keep the concepts separate.

---

# 8. The live environment and the target environment are different worlds

A huge percentage of installer mistakes come from forgetting this.

The live installer environment:
- runs from the ISO
- is temporary
- contains installer tools
- has its own `/etc`, `/usr`, `/var`, maybe even its own users
- may be writable only through a temporary runtime layer
- disappears on reboot unless persistence is deliberately designed

The target environment:
- lives on the user’s disk
- is permanent
- has its own `/etc`, `/usr`, `/var`, `/home`
- must boot independently from the installer medium
- is the system users will actually live in

These are separate filesystems and separate lifecycles.

---

## 8.1 Live image format and writable runtime layer

A modern Arch live environment normally boots from a **read-only compressed live root image** and presents a writable runtime view through additional live-session mechanisms.

The exact implementation details can change over time, so the durable mental model is:

- the live root image is read-only
- the running live system appears writable
- writes during the live session are temporary unless persistent storage is explicitly designed

That is why:
- the installer can create temporary files, logs, mounts, and runtime state
- the source installation medium itself remains unchanged
- your live-session edits generally disappear after reboot

This is normal live-media behavior.

---

## 8.2 Why the target packages should not be “the live root”

Your target system should be constructed from package artifacts in the local repo, not from a blind copy of the live installer root.

Why?

Because the live installer may contain:
- installation-only tools
- debugging tools
- live boot assumptions
- live-session services
- temporary defaults
- demo/live-user config
- files that are correct for the installer but wrong for the final system

The target system should be built intentionally as a package-selected installation, not accidentally as a snapshot of the installer runtime.

---

# 9. Your bare Git dotfiles repo is not directly usable as `/etc/skel`

This is another extremely important distinction.

A bare Git repo is just the Git database:
- objects
- refs
- metadata

It does **not** contain a normal working tree by default.

That means a bare repo is not the same thing as a ready-to-copy filesystem tree.

If you want your installed user to receive files like:
- `.config/hypr/...`
- `.bashrc`
- `.zshrc`
- `.config/waybar/...`
- user helper scripts

then you need a **checked-out file tree**, not just the bare repo storage itself.

So in the Factory, your process must conceptually be:

1. choose a specific commit of your dotfiles repo
2. export or check out the working-tree contents
3. turn those files into a payload for the target system
4. place that payload into the target system before user creation

That is the correct flow.

---

## 9.1 What Git does and does not preserve

Git tracks:
- file contents
- directory structure
- symlinks
- the executable bit for regular files

Git does **not** normally preserve:
- ownership
- arbitrary Unix permission detail beyond the usual executable-bit distinction
- all filesystem metadata you may care about in system packaging contexts

So if your dotfiles include scripts that must be executable, the executable bit must be correct in Git or in whatever packaging step produces the final payload.

Ownership will not come from Git.
Ownership will be determined by how the files are installed or copied later.

That is one reason why packaging can be cleaner than ad hoc file copying.

---

# 10. What `/etc/skel` actually is

`/etc/skel` is the **skeleton directory** used as a template when creating new user home directories.

Think of it as the **birth template** for a home directory.

It is not:
- a live link
- a dynamic overlay
- a magical always-synced source of truth for existing users

It is a **copy-on-user-creation template**.

When a new user is created with home creation enabled, the system can copy the contents of `/etc/skel` into the new home.

After that:
- the user’s home contains independent copies
- changes to `/etc/skel` later do not rewrite that existing user’s files

That one-time-copy behavior is crucial.

---

## 10.1 What belongs in `/etc/skel`

Good candidates include:
- shell rc/profile files
- Hyprland config
- Waybar config
- launcher config
- notification config
- user-local helper scripts
- terminal config
- editor config
- per-user app preferences
- user-scoped service definitions, if that is your design

These are the kinds of things that are:
- personal
- user-owned
- expected to be editable by the user
- appropriate to seed into each new home

---

## 10.2 What does not belong in `/etc/skel`

Bad candidates include:
- machine identity
- package-manager repo config
- system service config
- large shared wallpapers
- icon themes
- cursor themes
- font payloads
- kernel or bootloader config
- package databases
- caches/logs
- secrets/tokens
- host-specific generated files

Those belong elsewhere.

A good rule is:

### If every user should get a private editable copy
`/etc/skel` may be appropriate.

### If the whole machine should share one copy
It probably belongs in a shared system location instead, often under `/usr/share` or another package-managed path.

---

# 11. The biggest `/etc/skel` trap in custom Arch installers

This is the most important practical gotcha in your whole design.

If you put files into the **live ISO’s** `/etc/skel`, that affects users created **inside the live environment**.

It does **not automatically** affect the installed target system.

The installed system has its own completely separate `/etc/skel`.

So if your installer later runs user creation against the target system, the skeleton files that matter are the ones in:

- **the target system’s `/etc/skel`**

not the live ISO’s.

This is such a common mistake that it is worth stating plainly:

> If the target user is created in the target system, then the target system’s `/etc/skel` must already contain the desired files before that user is created.

That is the invariant.

---

## 11.1 The two clean ways to get your dotfiles into target `/etc/skel`

### Approach A — Custom package
Create a package that installs your checked-out dotfiles payload into the target system’s `/etc/skel`.

This is usually the cleanest architecture.

Why it is good:
- versioned
- reproducible
- natural to include in your local repo
- updateable later
- managed like the rest of the system payload

### Approach B — Installer copy step
Have your installer explicitly copy a prepared payload from the ISO/live environment into the target root before creating the user.

This can work, but it is more ad hoc.

The key requirement stays the same:
the target `/etc/skel` must be populated **before** target user creation.

---

# 12. How user creation handles `/etc/skel`, ownership, and permissions

A natural question is:

> If `/etc/skel` is root-owned system content, how does it become safe, user-owned config in the new home?

The answer lies in understanding the difference between:
- ownership
- file mode bits

These are related but distinct.

---

## 12.1 The basics of file metadata

A file has metadata such as:
- owner
- group
- permission mode bits
- timestamps
- file type
- and possibly ACLs/xattrs depending on filesystem and tooling

The classic permission bits are:
- read
- write
- execute

for:
- owner
- group
- others

Changing ownership does **not automatically change the mode bits**.

That is the key fact.

---

## 12.2 What user creation does conceptually

When you create a user with home-directory creation enabled, the account-management process generally does something conceptually like this:

1. allocate the user identity
   - UID
   - primary group
   - username
   - shell
   - home path

2. create the home directory if needed

3. copy the contents of `/etc/skel` into that new home

4. assign ownership of the new home files to the new user

5. preserve normal file modes

That means:
- root-owned template files are copied
- the copies become owned by the new user
- executable scripts remain executable if their mode bits were correct
- private files remain private if their mode bits were correct

This is exactly how `/etc/skel` is meant to work.

---

## 12.3 Why executable scripts stay executable

Suppose a script in `/etc/skel` is executable.

When it is copied into the user’s home:
- the content is copied
- the executable bit is preserved
- the ownership is changed to the user

So the script remains executable.

That is because:
- execute permission is part of the file mode
- ownership is a separate property

This is why your helper scripts can be safely templated into `/etc/skel` as long as the executable bit is correct in the source payload.

---

# 13. The cleanest mental model for your own project

At this point, the most useful possible summary is this:

Your custom offline Arch ISO should be thought of as **four logical payloads**.

## Payload 1 — Installer payload
Audience:
- the live installer environment only

Belongs in:
- the live environment build, usually via packages plus overlay/customization

Contains things like:
- installer scripts
- helper logic
- diagnostics
- live-session package config
- trust/keyring state needed for offline install
- live branding if desired

---

## Payload 2 — Offline package repository payload
Audience:
- `pacman` / `pacstrap` during installation

Belongs in:
- the local repository on the ISO

Contains:
- official Arch packages
- prebuilt AUR packages
- your own custom packages
- repo metadata
- optional signatures

---

## Payload 3 — Target machine-policy payload
Audience:
- the installed machine as a system

Belongs in:
- target `/etc` and other machine-policy locations as appropriate

Contains things like:
- package-manager target policy
- login/session policy
- networking/service configuration
- machine-wide defaults
- admin overrides
- boot-related policy/config

---

## Payload 4 — Target user-defaults payload
Audience:
- newly created users on the installed system

Belongs in:
- target `/etc/skel`

Contains things like:
- Hyprland config
- Waybar config
- launcher config
- shell config
- user helper scripts
- per-user app defaults
- user-scoped service definitions, if applicable

This four-payload model is the clean architecture.

---

# 14. What Part 2 and Part 3 will cover

This first part established the foundation.

The next two parts should build on this in order:

## Part 2
A precise map of **what belongs where** in the final system and installer, including:
- ISO root
- live environment
- local repo
- target `/etc`
- target `/usr/share`
- target `/etc/skel`
- target `/home`
- package-managed payloads vs user config vs shared assets

It will also map the full Hyprland desktop stack component-by-component.

## Part 3
The full **lifecycle story and implementation architecture**, including:
- from Factory to ISO to live boot to target install to first boot
- service activation and startup strategy
- trust/keyring lifecycle
- target repo policy after install
- common failure modes
- the cleanest maintenance workflow for rebuilds every few weeks

---

# Condensed takeaway for Part 1

If you remember only a few things from this part, remember these:

1. **An offline Arch ISO is not one thing.**  
   It is a live installer environment plus a local package repository plus a target system definition.

2. **The live environment is not the target system.**  
   Never confuse the installer root with the installed root.

3. **A local repo is not just a folder of package files.**  
   It needs repository metadata and a trust model.

4. **The AUR is not a binary repo.**  
   For your design, AUR software must be prebuilt into binary packages in the Factory.

5. **Your dotfiles bare repo is not directly installable as user config.**  
   You need a checked-out working-tree payload.

6. **The target user’s files come from the target system’s `/etc/skel`, not the live ISO’s `/etc/skel`.**

7. **The target `/etc/skel` must be populated before the target user is created.**

---


# Part 2 of 3 — What belongs where: ISO layout, target filesystem layout, and the full Hyprland component map

> **Purpose of this part**
>
> Part 1 explained the architecture:
>
> - Factory
> - live installer environment
> - offline local repository
> - target installed system
>
> This part answers the next question:
>
> **Where should each class of file or component actually live?**
>
> That means:
>
> - what belongs on the ISO itself
> - what belongs only in the live installer environment
> - what belongs in the local package repo
> - what belongs in target `/etc`
> - what belongs in target `/usr/share`
> - what belongs in target `/etc/skel`
> - what eventually belongs in the created user’s home
>
> This is the part that prevents the most common design mistakes.

---

# 1. The master rule for file placement

A file belongs somewhere because of **three questions**:

1. **Who needs to read it?**
2. **When do they need to read it?**
3. **Should there be one shared copy, or one copy per user?**

That is the entire logic.

If you answer those three questions correctly, placement becomes much easier.

---

## 1.1 The four big categories of content

Nearly everything in your project falls into one of these categories:

### Category A — Installer runtime content
Things needed only while booted into the installer.

Examples:
- installer scripts
- partitioning helpers
- live-specific `pacman` config
- diagnostics
- live-session services
- live-only branding/docs

These belong in the **live installer environment**.

---

### Category B — Install source content
Things `pacman` needs in order to build the target system.

Examples:
- official package archives
- prebuilt AUR package archives
- your custom package archives
- repository metadata
- optional signatures

These belong in the **local repository on the ISO**.

---

### Category C — Machine-wide target content
Things that define how the installed machine behaves as a system.

Examples:
- service configuration
- package-manager target policy
- login/session policy
- locale/time/input defaults
- networking configuration
- boot-related policy
- admin overrides

These usually belong in **target `/etc`** and related machine-policy locations.

---

### Category D — User-default and shared-asset content
This category splits in two:

#### D1 — Per-user default config
Things that every new user should receive as their own editable copy.

Examples:
- Hyprland config
- Waybar config
- shell config
- launcher config
- user helper scripts

These belong in **target `/etc/skel`**.

#### D2 — Shared system resources
Things all users and applications can reference from one shared copy.

Examples:
- wallpapers
- fonts
- icon themes
- cursor themes
- toolkit themes
- desktop entry data
- shared branding assets

These usually belong in **target `/usr/share`** or another package-managed shared location.

---

# 2. The ISO image itself

The `.iso` file is not just a folder of files.

It is a bootable distribution artifact with multiple roles.

Conceptually, it contains at least:

- installer boot assets
- the live root image for the installer environment
- the local repository for offline package installation
- optional release metadata

---

## 2.1 The ISO root

The ISO root is the top-level storage layout of the installation medium itself.

This is **not** the same thing as `/` after the live system boots.

That distinction matters.

The ISO root is the storage layer that holds:
- boot assets needed before the live system starts
- the live root image used by the installer
- the offline package repository
- optional release manifests/checksums/docs

You should think of the ISO root as a **transport layer**.

It is where artifacts live when they must remain directly accessible on the installation medium.

---

## 2.2 What usually belongs on the ISO root

### A. Installer boot assets
These are the files and boot structures needed to start the installer medium.

Conceptually this includes:
- bootloader assets for the installer medium
- EFI-visible installer boot files
- the live kernel and live initramfs
- the structures that make the image bootable as installation media

These are for **booting the ISO**, not for booting the final installed system.

---

### B. The live root image
The live installer environment is normally stored as a **compressed read-only live root image** on the ISO.

This image is later located and mounted by the live-boot path.

Do not confuse this image with:
- the installed root filesystem on the target disk
- a package repository
- the final target OS

It is only the installer operating system image.

---

### C. The local offline repository
This is one of the most important things on the ISO.

It contains:
- official Arch packages
- prebuilt AUR packages
- your custom packages
- repository metadata
- optional signatures

This repository must remain visible as ordinary files on the installation medium so the live environment can point `pacman` at it.

---

### D. Optional release metadata
These are not strictly required for installation, but are very useful.

Examples:
- build manifests
- package lists
- checksums
- build date
- release name/version
- dotfiles commit ID
- release notes
- internal documentation

If you are distributing the ISO to other people, including this kind of metadata is good practice.

---

## 2.3 What should not live only on the ISO root

A file should **not** exist only on the ISO root if the live installer environment needs it as part of its normal root filesystem.

Examples:
- an installer script the live environment must execute
- a live-session service unit
- a live-session config file
- a helper binary expected in the live root

Those belong in the **live environment build**, not only as loose files sitting on the medium.

Likewise, content intended to be installed into the target system should generally be represented as **packages in the local repo**, not as arbitrary loose files copied by custom scripts unless you have a strong reason otherwise.

---

# 3. The live installer environment

When the user boots the ISO, they are running a temporary operating system.

This environment is separate from:
- the ISO storage layout
- the local repo
- the final target system

Its job is to:
- boot
- provide tools
- run your installer logic
- install the target system from the local repo

---

## 3.1 What the live environment is for

The live environment exists to perform installation.

That means it needs:
- shell and basic userspace
- `pacman` / `pacstrap`
- storage tools
- filesystem tools
- mount logic
- your installer scripts
- trust/keyring state for offline verification
- any helpers needed by your install workflow

Optionally, it may also include a graphical desktop if you want a demo/live environment. But that is a separate design choice.

---

## 3.2 What belongs in the live installer environment

### A. Core live userspace
These are the basics needed for the live OS to function:
- shell
- core utilities
- init/service management for the live environment
- package tools
- editor/pager if you want one
- compression/archive helpers
- debugging tools you want available during install

---

### B. Partitioning and filesystem tools
These are installer tools, not final target policy.

Examples conceptually:
- partitioning tools
- filesystem creation tools
- filesystem check tools
- mount helpers
- block-device inspection tools

These belong in the live environment because the installer must use them before the target system even exists.

---

### C. Installer orchestration logic
This is where your installer scripts live.

Examples:
- disk selection logic
- partition plan logic
- filesystem creation workflow
- mount planning
- package-selection logic
- target configuration helpers
- user-creation helpers
- boot-setup helpers
- post-install orchestration

If the live environment must execute the script, that script belongs in the live environment.

---

### D. Live-specific package-manager configuration
The live environment needs its own `pacman` context.

That includes:
- the repository path(s) for the offline install source
- verification policy
- trust/keyring state sufficient for offline verification
- any live-only package-manager behavior

This is **not automatically the same thing** as the installed target system’s package-manager config.

---

### E. Live-only helper content
Examples:
- offline documentation
- sanity-check scripts
- diagnostics
- hardware-detection helpers
- user prompts/menus
- release metadata copied into the live environment for convenience

These are installer-runtime content, not target-system content.

---

## 3.3 What usually does not belong in the live environment

The live environment should not be treated as “the whole final desktop by default.”

Things that belong only in the **target local repo** or **target system** generally do not need to be installed into the live root unless the live session actually needs them.

That means a CLI-only installer ISO does **not** need to install your entire Hyprland desktop stack into the live environment just because the target system will use it later.

Those packages can remain in the local repo for installation into the target.

This keeps the architecture cleaner and reduces duplication.

---

# 4. The local offline repository on the ISO

This is the package warehouse from which the target system is assembled.

It is one of the core pillars of the whole design.

---

## 4.1 What belongs in the local repo

### A. Official Arch packages
Anything the target system must install offline.

Examples include:
- base userspace
- kernel
- firmware
- microcode, if you ship it
- bootloader packages
- filesystem tools needed on the installed system
- networking stack
- graphics stack
- Hyprland and companion desktop packages
- audio stack
- fonts
- themes
- browser/editor/core apps
- your preferred shell and utilities

You must include the full dependency closure, not just your top-level package list.

---

### B. Prebuilt AUR packages
Any AUR-derived software you want available offline must be prebuilt in the Factory and included here as binary packages.

At install time these are just normal package artifacts in your local repo.

---

### C. Your custom packages
This is where your own packages should live.

Good examples:
- user-defaults package for target `/etc/skel`
- shared-assets package for wallpapers/icons/themes/fonts/branding
- machine-policy package for target `/etc`
- session/login integration package
- helper-tools package for system-wide utilities

This is often much cleaner than managing everything with raw copy scripts.

---

### D. Repository metadata
Without repository metadata, the local repo is just a folder of package files.

With repository metadata, `pacman` can:
- resolve dependencies
- choose versions
- install by package name
- behave normally offline

This is mandatory for a clean installer design.

---

### E. Optional signatures and related trust artifacts
Depending on your trust model, this may include:
- package signatures
- repo metadata signatures
- associated trust planning

Exactly how you implement trust can vary, but the repo must be consistent with the verification policy used by the live environment.

---

## 4.2 What does not belong in the local repo

The local repo should contain **installable package artifacts**, not general random content.

Things that do not usually belong there:
- arbitrary shell scripts not packaged
- raw source trees
- your bare dotfiles repo as Git object storage
- personal notes
- random archives unrelated to package installation

If something is not meant to be consumed by `pacman` as part of the target package graph, it probably should not live in the local repo.

---

# 5. The target installed system: the big filesystem split

Once `pacstrap` installs packages into the target root, the system begins to take shape.

At a high level, the target installed system contains several distinct roles:

- boot-related areas
- `/etc` for machine-wide policy
- `/usr` for package-managed software and shared resources
- `/usr/share` for shared data/assets
- `/etc/skel` for default user-home templates
- `/home` for actual created users
- `/var` for mutable system state

These are not interchangeable.

---

# 6. Target boot-related areas

A very common mistake is to blur together:

- booting the ISO
- booting the installed system

They are separate.

The target system has its own boot path, its own boot artifacts, and its own final boot configuration.

---

## 6.1 What belongs to the target boot path

Conceptually, target boot-related content includes:
- the installed system’s kernel artifacts
- the installed system’s initramfs or equivalent early-boot artifacts
- installed bootloader assets
- boot entries or boot configuration
- EFI-visible files on the target ESP, where applicable

These belong to the **target system**, not the installer medium.

---

## 6.2 Why mount order matters

Before kernel/initramfs generation and target bootloader setup, the target’s relevant mounted filesystems must be mounted correctly.

If not, boot artifacts may be written to the wrong place.

This is not unique to offline installation, but it is one of the easiest mistakes to make in a scripted installer.

Correct boot partition mounting is part of target-system correctness.

---

# 7. Target `/etc` — machine-wide system policy

`/etc` is the installed machine’s configuration and policy layer.

It answers the question:

> **How should this installed machine behave?**

This is different from:
- user preference
- package payload data
- shared theme/image/font assets

`/etc` is primarily for machine-wide configuration, policy, and admin overrides.

---

## 7.1 What belongs in target `/etc`

### A. System identity and baseline behavior
Examples:
- hostname policy
- locale settings
- timezone selection
- machine-wide keyboard/input defaults
- package-manager target policy
- trust and verification policy

---

### B. Service configuration
Examples:
- networking service configuration
- Bluetooth service configuration
- login/session manager configuration
- daemon-specific configuration
- machine-wide audio/media policy if your design uses it
- administrative overrides for services

---

### C. Desktop-wide defaults that are truly machine policy
Examples:
- global environment settings
- machine-wide XDG/system defaults
- session-launch policy
- portal-related global policy where applicable
- toolkit defaults intended for all users
- admin-controlled desktop behavior

---

### D. Boot and init policy
Examples:
- initramfs generation policy
- bootloader-related config or references
- machine-wide boot behavior choices
- kernel command-line policy, where applicable

---

### E. Administrative overrides
Many packages ship vendor defaults in package-managed locations under `/usr`.

If you want the installed machine to override those defaults, the override generally belongs in `/etc`.

This is a very common Unix design pattern:

- package/vendor defaults under `/usr`
- machine/admin policy under `/etc`

---

## 7.2 What usually does not belong in target `/etc`

Things that are usually **not** system policy should not be forced into `/etc`.

Examples:
- your personal Hyprland keybindings
- your Waybar CSS
- your shell aliases
- your launcher theme config
- private per-user helper scripts
- large wallpapers
- fonts and icon-theme payloads
- user-owned app preferences

Those usually belong either in:
- **target `/etc/skel`** as user defaults
or
- **target `/usr/share`** as shared assets

---

# 8. Target `/usr` and `/usr/share` — package-managed software and shared resources

Most package-managed software payload lives under `/usr`.

For your purposes, the most important shared-data area to understand is `/usr/share`.

---

## 8.1 What target `/usr` is for

Broadly, `/usr` contains package-managed software and support data, such as:
- executables
- libraries
- shared support files
- documentation
- desktop/session integration files
- shared data resources

This is where most package payload ends up.

That means:
- Hyprland the program is package-managed software
- your terminal emulator is package-managed software
- graphics libraries are package-managed software
- many desktop integration files are package-managed software

They are not hand-placed into `/etc/skel`.

---

## 8.2 What target `/usr/share` is for

`/usr/share` is the classic home for architecture-independent shared data.

Think of it as the system’s **shared resource library**.

It is often the right place for:
- wallpapers
- lockscreen images
- icon themes
- cursor themes
- toolkit themes
- fonts and text resources
- desktop entry data
- session descriptors
- MIME metadata
- application resource files
- package-provided examples and presets
- branding assets

These are things that:
- can be shared by all users
- are not primarily machine policy
- are not primarily per-user editable config

---

## 8.3 Good candidates for target `/usr/share`

### A. Shared visual assets
Examples:
- wallpapers
- lockscreen images
- icon themes
- cursor themes
- theme assets
- sound themes
- branding artwork

---

### B. Shared desktop integration data
Examples:
- session descriptors
- desktop entry files
- MIME metadata
- application metadata
- package-provided default schemas/presets
- other shared app resources

---

### C. Fonts and related text resources
Examples:
- system fonts
- icon fonts
- fallback fonts
- text-rendering support files

---

### D. Package-provided examples and resources
Examples:
- sample configs
- locale files
- docs/help data
- shell completions and related support data
- static resource bundles shipped by applications

---

## 8.4 What usually does not belong in `/usr/share`

Examples:
- mutable personal config
- machine-specific admin policy
- private user scripts intended to be edited by each user
- host-specific generated files
- package-manager local state
- logs/caches

Those belong elsewhere.

---

# 9. Target `/etc/skel` — the default home template

This is the key bridge between the installed system and the created user’s home.

`/etc/skel` answers the question:

> **What files should a newly created user start with in their home directory?**

This is where your user-default layer belongs.

---

## 9.1 Good candidates for target `/etc/skel`

### A. Shell defaults
Examples:
- shell rc files
- shell profile files
- aliases
- prompt settings
- shell functions
- per-user environment choices that are personal workflow preferences

---

### B. Hyprland user config
Examples:
- compositor config
- keybindings
- workspace behavior
- startup commands
- animation choices
- window rules
- monitor defaults, if you use them carefully

Important caution: monitor config can be hardware-specific and may need to be conservative if the ISO is for multiple users/machines.

---

### C. Waybar or other bar/panel config
Examples:
- layout
- modules
- styling
- custom script references

---

### D. Launcher config
Examples:
- menu behavior
- shortcuts
- theme selection
- user-level appearance choices

---

### E. Notification config
Examples:
- placement
- style
- timeout choices
- user-session startup preferences

---

### F. User-local helper scripts
Examples:
- screenshot wrappers
- clipboard helpers
- menu helpers
- utility shortcuts
- launch wrappers
- app chooser scripts

These are appropriate if they are intended to be user-owned workflow tools.

---

### G. Per-user application defaults
Examples:
- terminal config
- editor config
- file-manager user prefs
- other personal application settings

---

### H. User-scoped service definitions
If your design uses user-level services, user-owned unit/config files may belong in the user template so that each new user receives their own copy.

---

## 9.2 What does not belong in `/etc/skel`

Examples:
- machine identity
- package repo configuration
- system service configuration
- fonts
- large wallpaper payloads
- icon themes
- cursor themes
- bootloader config
- kernel config
- package manager databases
- logs/caches
- secrets/tokens
- host-specific generated content

A good rule is:

### If it should exist once per machine
Do **not** duplicate it through `/etc/skel`.

### If it should exist once per user as editable home content
`/etc/skel` is a good candidate.

---

# 10. Target `/home` — what `/etc/skel` turns into

`/etc/skel` is only the template.

`/home/username` is the real result.

After user creation:
- files are copied from target `/etc/skel`
- ownership changes to the new user
- the user’s home becomes independent from the template

That means:
- the user can edit their config
- later changes to `/etc/skel` do not rewrite that existing home
- `/etc/skel` is for initial provisioning, not ongoing sync

This is a crucial design limitation to remember.

If you later want ongoing synchronization of user config, that is a different system entirely.

---

# 11. Target `/var` — mutable system state

You do not usually hand-design `/var` the way you design `/etc` or `/etc/skel`, but you need to understand its role.

`/var` contains mutable system state such as:
- package-manager local database/state
- logs
- service state
- caches
- spool/state files
- runtime-persistent app/service data

When `pacstrap` installs packages, the target package database is populated as part of this state.

This is why the installed system is a real package-managed system, not just a pile of copied files.

---

# 12. The Hyprland desktop stack: component-by-component placement

Now that the filesystem roles are clear, we can map the desktop stack itself.

This is the practical heart of the guide.

A Hyprland-based system is not one component.
It is a graph.

Each component belongs to a different layer.

---

## 12.1 Base userspace

### What it is
The underlying Arch system everything else runs on.

### Where it belongs
- as packages in the **local repo**
- installed into the target under package-managed locations, mostly under `/usr`
- with machine config in `/etc`
- and mutable state under `/var`

### Why it matters
Before “desktop” means anything, the installed system has to be a functioning Linux system.

---

## 12.2 Target kernel, firmware, and microcode

### What they are
The kernel and hardware-support pieces for the installed system.

### Where they belong
- package archives in the **local repo**
- installed into the **target system**
- with resulting boot artifacts placed into the target boot layout

### Important distinction
These are target-system components.
They are not the same thing as the live installer’s kernel and initramfs.

---

## 12.3 Bootloader for the installed system

### What it is
The target machine’s own boot path.

### Where it belongs
- in the target boot/EFI structure
- with related machine policy/config in target configuration

### Important distinction
This belongs to the target install phase, not to the ISO build phase.

The ISO has its own boot story.
The installed machine has another.

---

## 12.4 Graphics stack and GPU userspace

### What it is
The userspace graphics support needed for rendering and acceleration.

Conceptually this includes:
- rendering libraries
- display/buffer interfaces
- vendor-specific userspace components where needed
- XWayland support if used

### Where it belongs
- as packages in the **local repo**
- installed into package-managed locations on the **target system**
- with any machine-wide graphics policy/tuning in `/etc` where appropriate

### What it is not
It is not a dotfiles problem.

No amount of user config can compensate for a missing or mismatched graphics stack.

---

## 12.5 Hyprland itself

### What it is
The compositor/window-management environment.

### Where it belongs
Hyprland the software:
- package archive in the **local repo**
- installed into package-managed system locations on the **target**

Hyprland your preferences:
- keybindings
- startup behavior
- animation choices
- window rules
- monitor defaults
- user environment preferences

These belong in **target `/etc/skel`** as default per-user config.

That is the correct split.

---

## 12.6 XWayland compatibility

### What it is
Compatibility support for running X11 applications in a Wayland session.

### Where it belongs
- package in the **local repo**
- installed into package-managed system locations on the **target**

### Why it matters
Many real-world applications still rely on this compatibility path.

---

## 12.7 Seat and session access infrastructure

### What it is
The infrastructure that mediates access to input devices, GPU/session state, and related user-interactive hardware/session resources.

### Where it belongs
- package-managed system software in the **target**
- machine-wide policy and service behavior in **target `/etc`** where applicable

### Important distinction
The installed software may be system-wide, but actual session behavior depends on your session/login model.

This is one of the places where package placement and runtime activation are not the same thing.

---

## 12.8 Login/session path

A desktop does not just “appear.”
Something must:
- authenticate the user
- create a session
- launch the compositor

There are two broad models:

### Model A — TTY-driven
User logs in on a text console and starts the compositor/session from there.

### Model B — Greeter/login-manager-driven
A greeter or login manager presents a login path and launches the session after authentication.

### Where the pieces belong
Software packages:
- in the **local repo**
- installed into package-managed locations on the **target**

Machine-wide behavior:
- in **target `/etc`**

Shared session descriptors or session metadata:
- often in package-managed shared locations such as **`/usr/share`**

User-specific session startup behavior:
- in **target `/etc/skel`** if it is meant as a per-user default

Do not collapse all login/session behavior into “just another dotfile.”
It is partly a system concern and partly a user-session concern.

---

## 12.9 Greeter or login manager

### What it is
The component that presents a login interface and launches a session.

### Where it belongs
Software:
- package in the **local repo**
- installed into package-managed target locations

Machine policy/config:
- **target `/etc`**

Shared session resources:
- often **target `/usr/share`**

User-specific session behavior:
- user config, when applicable

This is fundamentally a machine-level integration point, not only a user-preference layer.

---

## 12.10 D-Bus and desktop IPC

### What it is
A major inter-process communication layer used throughout modern Linux desktops.

### Why it matters
Many desktop components and applications depend on it:
- notifications
- portals
- privilege mediation helpers
- settings helpers
- application integration
- various desktop-aware utilities

### Where it belongs
- base/desktop-related packages in the **local repo**
- installed into package-managed target locations
- with machine policy where relevant in `/etc`

Not everything here is a boot-time system daemon in the simplistic sense, but the package/runtime infrastructure is still part of the target system.

---

## 12.11 XDG desktop portals

### What they are
A desktop-integration mechanism used by many modern applications, especially for:
- file pickers
- screenshots
- screencasting/screen sharing
- URI opening
- desktop-service mediation

### Where they belong
Software:
- packages in the **local repo**
- installed system-wide on the **target**

Global policy/defaults:
- in **target `/etc`** if your setup uses them there

Shared integration data:
- often under package-managed shared locations like **`/usr/share`**

User-session behavior:
- influenced by the session environment and user runtime context

### Important note
Portals are not “just a package to install.”
They are part of a broader desktop/session integration story.

---

## 12.12 Polkit / privilege mediation

### What it is
A policy-mediated path for desktop applications and tools to request privileged operations.

Examples of actions that may rely on this kind of path:
- mounting removable media
- changing some system settings
- some network operations
- some power-management-related actions

### Where it belongs
Core framework:
- package in the **local repo**
- installed into package-managed locations on the **target**

Machine policy:
- in **target `/etc`** where appropriate

Desktop-facing/authentication agent behavior:
- often tied to the user session model, not merely package installation

The software is system-level.
The user-facing interaction often happens in the session layer.

---

## 12.13 Notification daemon

### What it is
The process that displays user notifications.

### Where it belongs
Software:
- package in the **local repo**
- installed system-wide on the **target**

Appearance and user behavior:
- **target `/etc/skel`**

Shared icons/themes/resources it uses:
- often **target `/usr/share`**

This is a good example of:
- program = package-managed system software
- style/startup behavior = user config
- icons/fonts/assets = shared resources

---

## 12.14 Status bar / panel

### What it is
The visible bar or panel that shows workspaces, clock, status modules, and related information.

### Where it belongs
Software:
- package in the **local repo**
- installed system-wide on the **target**

Layout/style/config:
- **target `/etc/skel`**

Shared resources it references:
- often **target `/usr/share`**

Do not confuse:
- the bar executable
with
- your personal bar layout and CSS

They are different layers.

---

## 12.15 Launcher / application menu

### What it is
The launcher used for starting applications, running commands, or switching windows.

### Where it belongs
Software:
- package in the **local repo**
- installed system-wide on the **target**

Theme/config:
- usually **target `/etc/skel`**

Shared theme assets:
- usually **target `/usr/share`**

---

## 12.16 Terminal emulator

### What it is
The graphical terminal application used inside the desktop session.

### Where it belongs
Software:
- package in the **local repo**
- installed system-wide on the **target**

Its user-facing config:
- usually **target `/etc/skel`**

Shared data/assets/support files:
- package-managed shared locations under `/usr`

Remember that:
- terminal emulator choice
and
- shell configuration
are different layers.

---

## 12.17 Shell

### What it is
The command interpreter and environment users interact with inside terminals and scripts.

### Where it belongs
Shell program itself:
- package in the **local repo**
- installed system-wide on the **target**

System-wide shell defaults:
- in **target `/etc`**

Per-user shell config:
- in **target `/etc/skel`**

This split is extremely important.

The shell executable is software.
Your aliases/functions/prompt/preferences are user defaults.

---

## 12.18 File manager

### What it is
The graphical file browser and related file-opening integration point.

### Where it belongs
Software:
- package in the **local repo**
- installed system-wide on the **target**

User preferences:
- usually **target `/etc/skel`**

Desktop integration data:
- package-managed shared locations, often under **`/usr/share`**

Related policy/mount/auth behavior:
- may involve `/etc`, D-Bus, privilege mediation, and removable-media integration

A file manager is not “just a GUI app.”
It often sits on top of several system and desktop integration layers.

---

## 12.19 Audio/media stack

### What it is
The userspace audio/video/media-routing infrastructure used by the desktop.

### Where it belongs
Core software:
- packages in the **local repo**
- installed system-wide on the **target**

Machine-wide defaults/policy:
- in **target `/etc`** where appropriate

User-session behavior:
- may also be influenced by the user session layer and related user config

This is another area where package installation and runtime activation are different questions.

---

## 12.20 Screen sharing / screencast path

### What it is
The multi-component path that allows applications to capture or share the screen in a Wayland-safe way.

### Where it belongs
It spans multiple layers:
- compositor support
- portal stack
- media stack
- user-session environment
- package-managed desktop integration

This is not one component and not one path.

That is exactly why it is easy to get wrong.

---

## 12.21 Screenshot tools

### What they are
Utilities for still-image screen capture.

### Where they belong
Software:
- package in the **local repo**
- installed system-wide on the **target**

User keybinds/workflow scripts/save-path logic:
- usually **target `/etc/skel`**

If they use shared theme/image resources:
- those usually belong in **target `/usr/share`**

Installing the tool is not the whole experience.
The workflow usually depends on user config.

---

## 12.22 Clipboard utilities

### What they are
Utilities for interacting with the clipboard in the Wayland session.

### Where they belong
Software:
- package in the **local repo**
- installed system-wide on the **target**

Workflow, keybinds, helper scripts:
- usually **target `/etc/skel`**

---

## 12.23 Lock screen and idle management

### What they are
The components that handle:
- locking the session
- idle detection
- related session behavior

### Where they belong
Software:
- package in the **local repo**
- installed system-wide on the **target**

User-facing behavior:
- often **target `/etc/skel`**

Shared visual assets:
- often **target `/usr/share`**

Machine-wide power behavior:
- may also involve `/etc` and system policy

---

## 12.24 Power management

### What it is
The machine’s suspend/lid/power/battery policy and related behavior.

### Where it belongs
Machine-wide policy:
- primarily **target `/etc`**

Related software/services:
- package-managed system software installed from the **local repo**

User shortcuts or convenience commands:
- may live in **target `/etc/skel`**

Do not try to express all power behavior solely through compositor config.
Some of it is machine policy.

---

## 12.25 Networking stack

### What it is
The software responsible for networking after installation.

### Where it belongs
Software:
- package in the **local repo**
- installed system-wide on the **target**

Machine-wide configuration:
- **target `/etc`**

Optional desktop-facing UI tools:
- system packages, with possible user-facing config layered on top

An install can be fully offline while the installed system is still expected to have working networking after first boot.

Those are separate concerns.

---

## 12.26 Bluetooth stack

### What it is
The software and services for Bluetooth devices.

### Where it belongs
Core software:
- package in the **local repo**
- installed system-wide on the **target**

Machine-wide service/config:
- **target `/etc`**

Desktop-facing user tools or UI behavior:
- may also have user config layered on top

---

## 12.27 Removable-media integration

### What it is
The path by which removable devices are detected, authorized, and presented to users.

### Where it belongs
It spans multiple layers:
- core system packages
- machine policy in `/etc`
- privilege mediation
- desktop integration
- file manager behavior
- user-session UX

This is another good example of something that is not “just one app.”

---

## 12.28 User dotfiles generally

### What they are
Your personal startup files and per-user app configs.

### Where they belong
As defaults in **target `/etc/skel`** so they become user-owned files at account creation.

Examples:
- shell config
- Hyprland config
- Waybar config
- launcher config
- terminal config
- user-level app preferences
- user startup scripts
- user unit definitions

### What they are not
They are not the whole operating-system definition.

Your dotfiles repo captures the user-default layer, not the entire distro architecture.

---

## 12.29 User workflow scripts

### What they are
Scripts that support your personal desktop workflow.

### Where they belong
If they are per-user tools:
- **target `/etc/skel`**

If they are intended as general system-wide utilities for all users:
- better as package-managed files in system paths under `/usr`

This distinction is important.
Not every script belongs in the home directory.

---

## 12.30 Per-user service units

### What they are
User-scoped services and related session-level units.

### Where they belong
User-owned units intended as part of your default user setup:
- **target `/etc/skel`**

Package-provided user-service files:
- package-managed system locations under `/usr`

Machine-wide policy/overrides:
- `/etc`, where appropriate

This is a good example of the broader rule:
- package payload
- machine policy
- user-owned config
are distinct layers

---

## 12.31 Fonts

### What they are
Shared text-rendering resources used by many parts of the desktop.

### Where they belong
As shared package-managed resources, usually under **target `/usr/share`** and related `/usr` locations.

### What belongs in user config
Only references to font family/style choices.

Do not put font payloads into `/etc/skel`.

---

## 12.32 Icon themes

### What they are
Shared icon resources.

### Where they belong
As shared assets, usually under **target `/usr/share`**.

### What belongs in config
Only the choice of icon theme, if needed.

Do not duplicate icon-theme payload into every user home.

---

## 12.33 Cursor themes

### What they are
Shared cursor assets.

### Where they belong
As shared assets, usually under **target `/usr/share`**.

### What belongs in config
Theme selection and size preferences, depending on your design.

---

## 12.34 Toolkit themes and styling assets

### What they are
Shared styling resources for GUI toolkits.

### Where they belong
Theme assets themselves:
- usually in **target `/usr/share`**

Selection/default behavior:
- may be machine-wide in `/etc`
- or per-user in `/etc/skel`, depending on your design

This is an important split:
- theme asset
- theme selection policy
are not the same thing

---

## 12.35 Wallpapers and lockscreen images

### What they are
Shared static visual assets referenced by desktop config.

### Where they belong
Usually best in **target `/usr/share`**.

### What belongs in user config
Only the path/reference choosing which wallpaper or lock image to use.

Do not duplicate large image assets through `/etc/skel` unless you intentionally want per-user copies.

---

## 12.36 MIME associations and desktop entries

### What they are
The metadata and defaults that help the system know:
- which apps exist
- how apps appear in menus
- what files/URIs they handle

### Where they belong
Shared metadata:
- package-managed shared locations, often under **target `/usr/share`**

Machine-wide defaults/policy:
- often **target `/etc`**

Per-user preferred handlers/defaults:
- can be seeded via **target `/etc/skel`**

This is another layered area:
- shared metadata
- system defaults
- user preference
are different things

---

## 12.37 Core application choices: browser, editor, and so on

### What they are
Your chosen day-to-day apps.

### Where they belong
Applications themselves:
- packages in the **local repo**
- installed system-wide on the **target**

Default user settings:
- usually **target `/etc/skel`**

Shared resources:
- package-managed locations under `/usr`

Be careful with any app profile seeding that may carry unwanted state, especially for things like browsers.

---

# 13. A practical test for deciding between `/etc/skel` and `/usr/share`

This one question is extremely useful:

> **If I create five users on this machine, should each user get their own private copy of this file?**

### If yes
It may belong in `/etc/skel`.

Examples:
- `.bashrc`
- Hyprland config
- Waybar config
- personal launcher config
- user helper scripts

### If no
It probably belongs in `/usr/share` or another shared package-managed location.

Examples:
- wallpapers
- fonts
- icon themes
- cursor themes
- theme assets
- branding images

This one rule prevents a lot of bad design.

---

# 14. CLI-only live ISO vs graphical live ISO

At this point, there is one major design fork to understand.

---

## 14.1 Model A — CLI-only live installer

In this model, the live environment only needs enough to:
- boot
- run shell scripts
- partition disks
- create filesystems
- mount the target
- use the local repo
- install/configure the target system

### Consequence
Most of the Hyprland desktop stack does **not** need to be installed into the live environment.

It only needs to exist as packages in the **local repo** so the target system can install it.

### Benefits
- cleaner architecture
- smaller live environment
- less duplication

---

## 14.2 Model B — graphical live ISO running your Hyprland setup

In this model, the live environment itself runs your desktop.

That means those desktop components now exist in **two different forms**:

1. installed into the **live installer environment**
2. also present as package archives in the **local repo** for installation into the target

### Benefits
- users can test-drive the environment
- stronger branding/demo effect

### Costs
- larger ISO
- more duplication
- more complexity
- live-user config and target-user config become separate concerns

And one crucial point still remains:

> Even if the live ISO runs your Hyprland config, that does **not** automatically mean the installed target user receives it.

You still need the target `/etc/skel` or equivalent target-side user-defaults payload.

---

# 15. The corrected “where does it go?” condensed map

This is the shortest useful summary of Part 2.

---

## On the ISO root
- installer boot assets
- live kernel/initramfs for the installer medium
- live root image for the installer environment
- local package repository directory
- optional release metadata, manifests, checksums

---

## In the live installer environment
- shell and core live userspace
- partitioning and filesystem tools
- `pacman` / `pacstrap`
- live-specific package-manager config
- trust/keyring state for offline verification
- installer scripts
- live-only helper files
- optionally a graphical live desktop if you intentionally support that model

---

## In the local repository
- official Arch packages for the target
- prebuilt AUR packages
- your custom packages
- repository metadata
- optional signatures

---

## In target `/etc`
- hostname/locale/time/input defaults
- package-manager target policy
- trust policy
- login/session machine policy
- networking/Bluetooth/service config
- machine-wide environment defaults
- boot/init policy
- administrative overrides

---

## In target `/usr/share`
- wallpapers
- lockscreen images
- icon themes
- cursor themes
- toolkit themes/assets
- fonts and text resources
- desktop entry data
- session descriptors
- MIME metadata
- shared app resources
- branding assets

---

## In target `/etc/skel`
- Hyprland config
- bar config
- launcher config
- shell config
- notification config
- user helper scripts
- terminal/editor app config
- user-level service/unit config
- other personal defaults new users should start with

---

## In the created user’s home
- the copied result of `/etc/skel`
- user-owned config
- later user edits and personal state

---

# 16. Condensed takeaway for Part 2

If you remember only the most important placement rules, remember these:

1. **The live installer environment is not the target system.**
2. **The local repo is for installable package artifacts, not random files.**
3. **Target `/etc` is for machine-wide policy.**
4. **Target `/usr/share` is for shared resources.**
5. **Target `/etc/skel` is for default per-user home content.**
6. **Your Hyprland desktop is a graph of packages, shared assets, machine policy, and user defaults—not just one config file.**
7. **A graphical live ISO and a graphical installed target are separate design choices.**

---

In **Part 3**, I’ll write the full corrected lifecycle and operational design:

- from Factory to ISO to live boot to target install to first boot
- trust/keyring lifecycle
- target repo policy after install
- runtime activation and startup strategy
- common failure modes
- the clean rebuild/release workflow for maintaining the ISO every few weeks

# Part 3 of 3 — The full lifecycle, trust model, startup model, maintenance workflow, and failure modes of a fully offline custom Arch ISO

> **Purpose of this part**
>
> Part 1 explained the architecture.
>
> Part 2 explained what belongs where.
>
> This part explains the **full lifecycle**:
>
> - how the project moves from Factory to ISO to target system
> - how offline trust actually works conceptually
> - how startup and activation work after installation
> - how to think about hardware variance
> - what decisions you must make about the installed system’s `pacman` config
> - what the common failure modes are
> - how to maintain and rebuild the ISO every few weeks as a real release artifact
>
> This is the part that ties everything together into one coherent operational model.

---

# 1. First, define the support boundary

Before building anything, you should explicitly define what your ISO **does** and **does not** support.

If you do not define this up front, the design will become confused later.

For a modern Arch-focused project, the clean support statement is usually something like this:

## 1.1 Supported design assumptions
- modern Arch Linux
- UEFI-based systems
- no legacy BIOS/CSM focus
- fully offline installation from local media
- package-managed target system built from a local `pacman` repository
- user defaults seeded into the target system before user creation
- reproducible rebuild cycle every few weeks

---

## 1.2 Decisions you must make explicitly
These cannot remain vague forever.

### A. Live ISO model
Will the live installer be:
- **CLI-only**, or
- **graphical**, running your Hyprland setup live?

Both are valid, but they are different architectures.

---

### B. Post-install repo model
Will the installed target system:
- use only normal online Arch repos after first boot?
- use Arch repos plus your own custom repo?
- use Arch repos plus a hosted binary repo you maintain?
- use only your preinstalled custom packages with no persistent custom repo afterward?

This affects:
- target `pacman` config
- trust setup
- maintenance burden
- update behavior later

---

### C. Trust model
Will your custom package/repo artifacts be:
- signed and trusted properly, or
- handled with a weaker trust model for your own repo content?

If other people will use this ISO, the stronger model is architecturally better.

---

### D. Secure Boot position
You must explicitly state one of:
- unsupported
- supported
- supported only with additional enrollment/signing steps

Do not leave this unstated.

---

### E. Hardware support policy
You must decide how broad your support target is:
- your own exact machine only
- a narrow hardware family
- general modern Intel/AMD systems
- systems including a broader range of GPU/network hardware

This affects:
- package selection
- default config safety
- testing scope
- user expectations

---

# 2. The full system as a release pipeline

A custom offline Arch ISO is best understood as a **release pipeline**, not just as a pile of files.

Each release is a coherent snapshot of several moving parts.

At the highest level, each rebuild cycle should produce:

1. a live installer environment
2. a local package repository snapshot
3. a machine-policy payload
4. a shared-assets payload
5. a user-defaults payload
6. a bootable ISO wrapper
7. release metadata for auditing and reproducibility

That is your actual product.

---

# 3. The Factory: where everything begins

The Factory is your build machine, VM, or controlled build environment.

It is the only place in the architecture that should touch the internet.

It is responsible for:

- selecting package sets
- gathering official Arch packages
- building AUR packages cleanly
- creating your own custom packages
- exporting your dotfiles payload from Git
- assembling the live installer environment
- building the ISO
- generating manifests/checksums/signatures as desired
- testing the output before release

---

## 3.1 Why the Factory matters so much

The Factory is where you freeze reality into a release snapshot.

If the Factory process is sloppy, your ISO will be sloppy in ways that are hard to debug later.

This is why clean builds, snapshot thinking, manifests, and testing matter.

The Factory is not just a place to “run commands.”  
It is your release-control environment.

---

## 3.2 What should be version-pinned or release-recorded

Each release should record, at minimum:

- build date
- Arch package snapshot date or equivalent release timestamp
- `archlinux-keyring`/trust snapshot state
- list of official packages included
- list of prebuilt AUR packages included
- exact versions of your custom packages
- dotfiles Git commit ID
- installer logic revision
- ISO release name/version
- release checksums
- release signature, if you sign release artifacts

Even if you are not publishing publicly yet, this is excellent discipline.

---

# 4. Stage 1 in the Factory — define the target system

Before downloading or building anything, define the **target installed system**.

Do not start by thinking about the live ISO.

Start by thinking about the final installed system.

---

## 4.1 The target package set

Your target package set should include all required categories, not just your favorite visible desktop apps.

At a high level, you need to think in terms of:

### Base platform
- base userspace
- kernel
- firmware
- microcode, if included
- bootloader-related packages
- filesystem tools required on target
- package manager and target repo policy

---

### Hardware support
- GPU userspace stack
- networking drivers/firmware support
- Bluetooth support, if intended
- laptop-specific support, if intended
- any hardware-specific support packages required by your audience

---

### Desktop core
- Hyprland
- XWayland, if included
- launcher
- bar/panel
- terminal emulator
- file manager
- notification daemon
- lock/idle tools
- clipboard tools
- screenshot tools

---

### Desktop integration stack
- D-Bus-related desktop support
- portal stack
- privilege mediation/auth path
- session/login path
- seat/session access infrastructure
- audio/media stack
- MIME/desktop integration

---

### Shared resources
- fonts
- icon themes
- cursor themes
- toolkit themes
- wallpapers
- branding assets

---

### Your custom payloads
- machine-policy package
- shared-assets package
- user-defaults package
- any custom helper-tool packages

This package definition stage is one of the most important design stages in the project.

---

## 4.2 Think in dependency closure, not top-level names

Your top-level package list is not enough.

Every target package pulls in dependencies, and your offline ISO must contain the **complete closure** needed for successful install.

That includes:
- runtime dependencies
- integration dependencies
- support libraries
- services and companion components
- package-manager trust dependencies
- anything needed for boot or basic operation

If you omit dependencies, your local repo may look complete while still failing in practice.

---

## 4.3 Hardware variance must be handled deliberately

This is where “my exact setup” and “something others can install” can conflict.

### Examples of hardware-sensitive areas
- Intel vs AMD vs NVIDIA graphics userspace
- Wi-Fi support differences
- Bluetooth controller support
- laptop power behavior
- monitor layout assumptions
- touchpad/input differences
- CPU microcode choice

If the ISO is meant for more than your own exact machine, your defaults must be conservative enough not to break other systems.

Especially important:
- **monitor rules in Hyprland config** can be dangerous as hard-coded per-user defaults
- graphics stack assumptions must match your support policy

So you need to decide whether your ISO is:
- personal-machine-specific
- family-of-machines-specific
- or broadly distributable

That changes how aggressive your defaults can be.

---

# 5. Stage 2 in the Factory — gather official packages as one coherent snapshot

Once the target package set is defined, the Factory gathers all official Arch packages needed for the target install.

This process should be treated as one coherent snapshot.

---

## 5.1 Why snapshot coherence matters

Arch is rolling release.

That means package compatibility depends heavily on coherent repository state.

If you gather packages at different times, you can create a mixed package universe where:
- one package expects one shared library version
- another package came from a later repo state
- an AUR package was built against older dependencies
- your keyring no longer matches the package set

That is how offline installers become brittle.

The solution is simple in concept:

> Treat each ISO build as a single release snapshot.

---

## 5.2 The official package snapshot is not enough by itself

A complete release snapshot includes:
- official package files
- repo metadata
- keyring/trust state
- built AUR package outputs
- custom package outputs
- dotfiles payload revision
- installer logic revision

Everything must line up.

---

# 6. Stage 3 in the Factory — build AUR packages cleanly

If you need AUR-derived software, it must be handled in the Factory.

For **this installer design**, the correct approach is:

- build the AUR package in the Factory
- produce a normal binary package artifact
- include that artifact in your local repo
- install it offline later like any other package

That is the clean design.

---

## 6.1 Why clean AUR builds matter

A clean build matters because it proves the package really declares what it needs.

Without clean builds, a package may succeed only because your host already had undeclared dependencies.

That is a distribution-quality failure.

If others will use your ISO, your AUR outputs need to be built in a way that minimizes:
- hidden host state
- accidental dependency leakage
- “works only on the build machine” behavior

---

## 6.2 AUR dependency recursion

Some AUR packages depend on:
- official Arch packages
- other AUR packages

That means your AUR build process also needs dependency closure thinking.

If one AUR package depends on another AUR package, both must be built and included appropriately.

---

## 6.3 VCS packages require extra discipline

If you ship packages built from moving VCS sources:
- source revision must be treated as part of the release
- reproducibility becomes weaker
- your release becomes harder to audit
- breakage risk increases

For a public offline ISO, fixed-release packages are usually safer.

---

# 7. Stage 4 in the Factory — build your own custom payloads

This is where your own customization becomes cleanly package-managed.

Instead of copying random files everywhere by hand, it is often better to define **payload classes**.

---

## 7.1 Payload class: machine-policy payload
This is your target-machine policy layer.

Examples:
- target `pacman` config
- login/session policy
- networking defaults
- machine-wide desktop defaults
- service config
- system environment defaults

This content belongs in:
- **target `/etc`**
- or other package-managed machine-policy locations as appropriate

---

## 7.2 Payload class: shared-assets payload
This is your shared resource layer.

Examples:
- wallpapers
- fonts
- icon themes
- cursor themes
- toolkit themes
- lockscreen images
- branding assets

This content belongs mostly in:
- **target `/usr/share`**
- or another shared package-managed location under `/usr`

---

## 7.3 Payload class: user-defaults payload
This is the payload built from your dotfiles working tree.

Examples:
- Hyprland config
- Waybar config
- launcher config
- shell config
- terminal config
- notification config
- user helper scripts
- user-level service definitions

This content belongs in:
- **target `/etc/skel`**

This is one of the most important payloads in the entire project.

---

## 7.4 Payload class: installer payload
This is content for the live environment only.

Examples:
- installer scripts
- diagnostics
- helper tools
- live package-manager config
- live trust/keyring setup
- live branding/docs

This belongs in:
- the live installer environment build

---

# 8. Stage 5 in the Factory — export your dotfiles correctly

Because your dotfiles repo is bare, you must not confuse:

- the Git object database
with
- a checked-out working tree

For the user-defaults payload, you need the actual file tree.

---

## 8.1 The correct mental flow for dotfiles

Conceptually:

1. choose a specific dotfiles commit
2. check out or export the working-tree contents
3. inspect and normalize file modes, especially executables
4. decide what is:
   - per-user config
   - shared assets
   - machine policy
5. put only the per-user default files into the user-defaults payload for target `/etc/skel`
6. move shared assets out into the shared-assets payload
7. move machine-wide policy out into the machine-policy payload

This is how you avoid treating the dotfiles repo as if it were the whole operating system definition.

---

## 8.2 Be very careful with hardware-specific dotfile content

Some config in your own home may be safe to distribute as defaults.
Some may not.

Potentially dangerous examples:
- hard-coded monitor layout directives
- absolute paths meaningful only on your machine
- machine-specific hostnames
- references to non-existent devices
- environment variables tied to your exact setup
- secrets/tokens/private credentials
- browser profile state
- stale caches

Your user-defaults payload should be a **clean default template**, not a copy of every artifact from your personal home directory.

---

# 9. Stage 6 in the Factory — trust and keyring preparation

This is one of the most important parts of a real offline installer.

Offline installation still needs a trust model.

---

## 9.1 The live installer needs local trust state

The live environment must be able to verify packages offline according to the verification policy you chose.

That means it must already contain:
- the relevant keyring/trust state
- current enough trust material for your package snapshot
- any supporting signature artifacts required by that policy

The exact implementation can vary, but the architectural rule is stable:

> The live installer must not need the network in order to verify the packages it is installing.

---

## 9.2 Keyring freshness is part of the release itself

This cannot be treated as an afterthought.

A stale keyring can break an otherwise correct offline installer.

So each ISO release must treat the trust/keyring state as part of the same coherent release snapshot as:
- official package files
- AUR-built package files
- your custom package files
- repo metadata

This is a release-management requirement, not an optional cleanup step.

---

## 9.3 If the installed system will keep using your custom repo, target trust must be configured

This is a point many people miss.

If your custom packages are only used during installation and never again afterward, then target-side trust for that repo may not matter after install.

But if the installed system will continue to use your custom repo later, then the target system must also be configured to trust that repo properly.

That means your release design must distinguish between:

### Install-time-only custom repo
- target may not need persistent trust/config for that repo later

### Persistently used custom repo
- target trust setup is mandatory
- target `pacman` config must be correct
- update strategy must be planned

---

# 10. Stage 7 in the Factory — build the live installer environment

Now the live environment is assembled.

Its purpose is not to be the final system.
Its purpose is to perform installation.

---

## 10.1 What the live environment must contain

At minimum:
- core live userspace
- package tools
- storage and filesystem tools
- your installer scripts
- live-side `pacman` configuration
- trust/keyring state for offline package verification
- any helper tools required by your installer logic

Optional:
- a live graphical desktop, if you intentionally support that model

---

## 10.2 CLI-only live installer vs graphical live installer

This decision has major consequences.

### CLI-only live installer
The live environment contains only what the installer needs.

Benefits:
- smaller
- simpler
- less duplication
- cleaner separation from target system

### Graphical live installer
The live environment itself runs your Hyprland desktop.

Benefits:
- demo value
- live preview

Costs:
- more duplication
- more complexity
- the live desktop config and target desktop config become separate concerns

Even in the graphical-live case, the target user still needs the target `/etc/skel` payload separately.

Do not confuse “the live session uses my config” with “the installed system now has my defaults.”

---

# 11. Stage 8 in the Factory — assemble the ISO as a release artifact

At this point, the release artifact is assembled from:
- installer boot assets
- live root image
- local package repository
- optional release metadata

This becomes the bootable ISO.

---

## 11.1 The ISO is a release artifact, not just a build byproduct

You should think of each ISO as a release with:
- a version
- a build date
- a package manifest
- a dotfiles commit ID
- an installer revision
- checksums
- possibly a signature on the release artifact itself

This mindset helps prevent “mystery ISOs” that no one can later explain or reproduce.

---

## 11.2 Include release metadata if others will use it

If this ISO will be distributed to other people, include enough metadata that a user—or future you—can answer:

- what packages are included?
- what custom packages are included?
- what dotfiles revision is embedded?
- when was this built?
- what hardware support is intended?
- what trust model is used?
- what update path is expected after install?

That level of release discipline matters.

---

# 12. The runtime story on the user’s machine

Now the Factory is done.
The ISO exists.
A user writes it to installation media and boots it.

From here on, the system moves through a sequence of runtime stages.

---

# 13. Stage 1 on the user’s machine — boot the installer medium

The machine starts by booting the installation medium.

Conceptually:
- firmware transfers control to the installer boot path
- the installer bootloader loads the live kernel and live early-boot environment
- the live installer root image is located and made available
- the live runtime becomes usable as a temporary operating system

The exact low-level mechanics can evolve over time, but the durable model is:

- boot installer medium
- start live installer OS
- run installer from there

---

## 13.1 The live system is temporary

The live system:
- is not the final installed system
- may run from a read-only image plus a writable temporary runtime layer
- can create temporary files and mounts
- generally loses those temporary changes on reboot unless persistence is intentionally designed

This matters because users often mentally treat the live OS as if it were the target OS.
It is not.

---

# 14. Stage 2 on the user’s machine — the installer prepares the target disk

Now your installer logic runs inside the live environment.

At this point, the target system does not yet exist.
There is only:
- a booted live OS
- a target disk
- a plan for installation

The installer must:
- identify the target disk
- partition as desired
- create filesystems
- mount the target root
- mount any target boot/EFI partitions correctly
- prepare the target root for package installation

Until that is done, the target system is only an empty mounted filesystem tree.

---

## 14.1 Why mount correctness matters so much

Before package installation and target boot setup, the target root and relevant boot partitions must be mounted in the right places.

If this is wrong, then:
- kernel artifacts can land in the wrong location
- bootloader assets can land in the wrong location
- initramfs generation can target the wrong mounted tree
- the installed system can appear “installed” but fail to boot later

This is one of the most common serious installer errors.

---

# 15. Stage 3 on the user’s machine — `pacman` / `pacstrap` installs the target system offline

Now the live environment uses the local repo on the installation medium as the source of truth.

This is the core of the offline install.

---

## 15.1 What happens conceptually during offline package installation

The package manager:
1. reads repository metadata from the local repo
2. resolves dependencies
3. selects package versions from the local repo snapshot
4. verifies packages according to the configured trust policy
5. reads package files from local media
6. extracts package payloads into the mounted target root
7. runs package scriptlets/hooks as applicable
8. updates the target system’s local package database

At the end of this process, the target root is a real Arch installation, not just a copied file tree.

---

## 15.2 What gets populated at this moment

This is where the target system receives:
- base userspace
- kernel and firmware packages
- bootloader-related packages
- graphics stack
- Hyprland and related desktop packages
- audio/media stack
- D-Bus/desktop integration packages
- portal stack
- your custom packages
- machine policy payload
- shared assets payload
- user-defaults payload

This is the moment where:
- target `/etc` starts to exist as a configured machine-policy layer
- target `/usr/share` starts to contain shared resources
- target `/etc/skel` starts to contain your user defaults

---

# 16. Stage 4 on the user’s machine — finalize target machine policy

After package installation, the target system exists as a package-managed Arch system, but installation is not done yet.

Now the installer finalizes the machine-specific configuration.

Examples of things that may happen here conceptually:
- hostname and locale configuration
- timezone selection
- target `pacman` repo policy configuration
- service configuration
- login/session policy configuration
- target boot policy configuration
- any remaining machine-specific configuration generation

This is where the target system becomes **this machine**, not just “some generic package set.”

---

## 16.1 This is also where target repo policy must become explicit

One of the easiest mistakes is to leave the target system’s package-manager config in an ambiguous or broken state.

You must deliberately choose one of these models:

### Model A — Installed system uses normal online Arch repos only
In this model:
- custom packages are preinstalled during the offline install
- after installation, the target no longer points to the installer medium as a repo
- future updates come from normal configured online repos
- your custom package update story must be considered separately

This is often simplest if your custom packages are install-time payloads and not part of a continuing repo relationship.

---

### Model B — Installed system uses Arch repos plus your hosted custom repo
In this model:
- the target points to normal Arch repos
- plus a real hosted custom binary repo you maintain
- trust for your repo must be configured in the target
- you now own the maintenance and update cadence for that repo

This is a cleaner long-term package-management model if you intend to keep shipping updates to your custom packages.

---

### Model C — Installed system points at a repo path that only existed on the installer medium
This is **bad design** unless explicitly intentional for some very narrow use.

Do not accidentally leave the installed system pointing at a dead local `file://` source from the ISO.

That is a broken post-install package policy.

---

# 17. Stage 5 on the user’s machine — create the user, but only after target `/etc/skel` is ready

This is one of the most important sequencing rules in the entire project.

The target user must be created **after** the target system’s `/etc/skel` is already populated with the desired default files.

That is how the user receives:
- Hyprland config
- shell config
- Waybar config
- launcher config
- user helper scripts
- other default home content

This is the correct order.

---

## 17.1 The correct order

The clean conceptual order is:

1. install target packages
2. ensure target `/etc` contains machine policy
3. ensure target `/usr/share` contains shared assets
4. ensure target `/etc/skel` contains user defaults
5. create the user in the target system
6. let the account-creation logic copy `/etc/skel` into the new home
7. ownership becomes user-owned in `/home/username`

That is the intended design.

---

## 17.2 Why this matters so much

If you create the target user too early:
- the home is created before the correct template exists
- the user does not receive the intended defaults
- later fixing `/etc/skel` does not automatically repair that already-created home

This is the number-one dotfiles-related installer trap.

---

# 18. Stage 6 on the user’s machine — finalize target boot setup

At this point, the target system’s package set and machine config are in place.

Now the final target boot path must be completed.

Conceptually this includes:
- ensuring the target boot partitions are mounted correctly
- ensuring target kernel/early-boot artifacts are generated into the proper target locations
- installing/configuring the target bootloader
- ensuring the target disk can boot independently from the installer medium

This is the transition from:
- “files installed on disk”
to
- “a bootable installed operating system”

---

# 19. Stage 7 on the user’s machine — first boot into the installed system

Now the machine reboots from its own disk, not from the installer medium.

This is where the runtime model of the installed system matters.

A huge source of confusion is thinking that once packages are installed, the desktop will simply appear automatically.
In reality, startup behavior is a graph of activation mechanisms.

---

# 20. The installed system has multiple activation layers

This is one of the most important corrections to the original notes.

Not everything starts the same way.

A modern Linux desktop involves several different activation domains.

You must understand these separately.

---

## 20.1 Activation domain A — Boot-time system services

These are machine-level services started as part of system boot.

Examples may include:
- networking services
- Bluetooth services
- system login/session infrastructure
- machine-level policy services
- some hardware-related services
- some core message-bus/system services

These belong to the **system boot/runtime layer**.

They are configured as machine policy.

---

## 20.2 Activation domain B — User login/session creation

This is the transition from:
- booted machine
to
- authenticated user session

This may happen via:
- TTY login
- a greeter/login manager
- some other session-launch path you explicitly support

This is not the same as “start all desktop apps at boot.”

It is the step that creates the user session context.

---

## 20.3 Activation domain C — User-session services and user-scoped units

Some desktop components are best thought of as user-session services rather than machine boot services.

Examples can include:
- user-scoped helpers
- session daemons
- some desktop integration helpers
- session-specific service units
- user environment tooling

These run in the **user session context**, not as machine boot services.

---

## 20.4 Activation domain D — D-Bus or equivalent on-demand desktop activation

Some desktop functionality is not started only by manual startup commands.
Some components may become active when needed through the desktop/session IPC model.

The exact implementation can vary, but the conceptual point is important:

Not every desktop component is a traditional always-on service manually started by the compositor config.

---

## 20.5 Activation domain E — Compositor/session startup commands

Some things are still launched from the session/compositor startup model:
- bar
- launcher-related helpers
- notification daemon
- wallpaper setter
- clipboard helpers
- screen lock helpers
- custom scripts

These may be:
- explicitly started by your user config
- or handled by user-scoped services
- or some combination depending on your design

The key point is that this is a **user-session concern**, not a machine-boot concern.

---

# 21. Why installation scope and runtime scope are different

This is another central design rule.

A package can be:
- installed system-wide
but
- run only in a user session

Or it can be:
- installed system-wide
and
- also activated as a machine service

Those are different questions.

Examples of the distinction:
- package placement tells you where the files live
- activation model tells you how the behavior actually starts

This distinction matters enormously in a Hyprland-based system.

Installing packages correctly is only half the job.
The other half is making sure the right things become active in the right context.

---

# 22. The first-boot runtime story, correctly understood

Once the installed system boots from disk, the sequence is conceptually:

1. firmware starts the target boot path
2. target bootloader loads target kernel and early-boot environment
3. target root filesystem becomes active
4. machine boot reaches the installed system userspace
5. boot-time system services start according to machine policy
6. a login/session path becomes available
7. the user authenticates or the configured login flow launches
8. the user session is created
9. user-session components become active by the mechanisms your design uses
10. Hyprland runs in that user session
11. user-facing components read config from the user’s home
12. shared assets are referenced from shared system locations
13. machine-wide policy remains in effect from `/etc`

That is the real runtime graph.

---

## 22.1 What reads from where on first boot

At first boot into the installed system:

### Machine policy reads from:
- target `/etc`

### Shared resources are read from:
- target `/usr/share` and related package-managed shared locations

### User-facing config is read from:
- the user’s home, which originally came from target `/etc/skel`

This is the intended architecture realized at runtime.

---

# 23. The trust lifecycle across the whole project

Trust is not a one-time step.
It has its own lifecycle.

---

## 23.1 Factory trust lifecycle
The Factory decides what is trusted from:
- official repos
- current Arch keyring/trust state
- AUR PKGBUILDs
- source tarballs
- your own source trees and assets

This is where trust enters the system.

---

## 23.2 Live installer trust lifecycle
The live installer must be able to verify packages offline using:
- shipped keyring/trust state
- shipped signature artifacts as required
- shipped repository metadata
- whatever verification policy the installer is configured to enforce

This is where trust is operationalized during install.

---

## 23.3 Target trust lifecycle
After installation, the target system’s trust state must make sense for the package repositories it will actually use.

This depends on your post-install repo model.

### If target uses only normal online Arch repos
Then the target trust model must be correct for that use.

### If target also uses your custom repo
Then your key/trust setup for that repo must also be present and correct on the target.

This is a permanent design decision, not a temporary installer convenience.

---

# 24. Secure Boot and release-signing considerations

Even if your main focus is installation architecture, these are important support-boundary topics.

---

## 24.1 Secure Boot must be an explicit support statement
Your project documentation must clearly state:
- unsupported
- supported
- or supported only with additional signing/key-enrollment steps

Do not leave this ambiguous.

Users need to know this before trying to boot the ISO or the installed system.

---

## 24.2 Release-artifact integrity is separate from package trust
Even if package trust is handled correctly, it is still good practice to provide:
- release checksums
- release signatures on the ISO artifact if you support that model

Why?
Because:
- users need to verify the ISO they downloaded
- package trust does not automatically guarantee release-download integrity
- you are distributing a system image, not just packages

These are different trust layers.

---

# 25. Licensing and redistribution review

If a lot of people will use the ISO, this is not optional as a release concern.

You must review redistribution rights for:

- AUR-built packages
- third-party binaries
- fonts
- icon themes
- wallpapers/art
- branding assets
- browser packaging/branding, if applicable
- any bundled media

Technical build success does not automatically mean redistribution is appropriate.

This is not a Linux mechanics issue.
It is a real distribution issue.

---

# 26. Hardware-variance strategy for a public ISO

If this is only for your own machine, you can hard-code much more.

If other people will use it, your defaults must be broader and safer.

---

## 26.1 Areas that need cautious defaults

### Graphics stack
Your package set and defaults should reflect your actual hardware support statement.

### Monitor config
Be careful with hard-coded monitor layout directives in Hyprland defaults.
What works perfectly on your machine may break another user’s first login experience.

### Input defaults
Touchpad, keyboard, and locale assumptions should be conservative.

### Power behavior
Laptop-focused defaults can be wrong on desktops and vice versa.

### Network assumptions
Do not assume one particular hardware path.

---

## 26.2 A good design principle
The broader your intended audience, the more your defaults should be:

- conservative
- portable
- hardware-tolerant
- easy to override

That is much more important in a public ISO than in a personal setup.

---

# 27. Common failure modes and what they really mean

This section is extremely important because these are the errors people actually hit.

---

## Failure 1 — Confusing the live environment with the target environment
### What goes wrong
Files are placed into the live system and assumed to affect the installed system automatically.

### Example
Putting your dotfiles only into the live environment’s `/etc/skel` and expecting the installed user to receive them.

### The real fix
Target-side content must be placed in the **target system**, not merely in the live environment.

---

## Failure 2 — Treating a package cache like a real repository
### What goes wrong
You have package files, but no proper repository metadata.

### Symptom
Dependency resolution fails or requires ugly manual handling.

### The real fix
Build a proper local repo with metadata, not just a random package dump.

---

## Failure 3 — Mixed snapshot state
### What goes wrong
Packages, AUR builds, and keyring state come from mismatched moments in time.

### Symptom
Dependency conflicts, broken runtime libraries, trust failures, random install failures.

### The real fix
Treat the ISO build as one coherent release snapshot.

---

## Failure 4 — Building AUR packages in a dirty host environment
### What goes wrong
Builds succeed only because undeclared dependencies happen to be installed on the build machine.

### Symptom
The package works for you but not for users.

### The real fix
Use clean controlled builds.

---

## Failure 5 — Stale keyring/trust state
### What goes wrong
The live installer’s trust state is older than the package snapshot.

### Symptom
Offline package verification fails even though the package files are present.

### The real fix
Treat keyring freshness as part of every release build.

---

## Failure 6 — Creating the user before target `/etc/skel` is ready
### What goes wrong
The home directory is created too early.

### Symptom
The installed user does not receive the intended config defaults.

### The real fix
Populate target `/etc/skel` first, then create the user.

---

## Failure 7 — Shared assets copied through `/etc/skel`
### What goes wrong
Fonts, wallpapers, icon themes, or other large shared resources get duplicated into every user’s home.

### Symptom
Wasted space, poor update behavior, confusing layout.

### The real fix
Put shared assets in shared package-managed locations, usually under `/usr/share`.

---

## Failure 8 — Machine policy put into user config
### What goes wrong
System-wide behavior is encoded only in user dotfiles.

### Symptom
The system becomes fragile, inconsistent across users, and hard to maintain.

### The real fix
Machine policy belongs in `/etc`; user preferences belong in `/etc/skel` or user homes.

---

## Failure 9 — Leaving the target pointing at a dead installer repo path
### What goes wrong
The installed system’s `pacman` config still references a repository path that existed only on the installer medium.

### Symptom
Post-install package management breaks.

### The real fix
Make target repo policy explicit and valid after installation.

---

## Failure 10 — Assuming package installation automatically creates a working desktop
### What goes wrong
Packages are installed, but runtime activation/startup is not designed properly.

### Symptom
The system boots, but:
- no bar
- no notifications
- no portal behavior
- incomplete session behavior
- pieces silently missing

### The real fix
Design activation and startup explicitly:
- boot-time machine services
- login/session flow
- user-session services
- compositor startup behavior
- on-demand desktop activation paths

---

## Failure 11 — Hard-coding your own monitor layout into public defaults
### What goes wrong
Your personal monitor assumptions become everyone’s first-boot config.

### Symptom
Broken or awkward first login on other hardware.

### The real fix
Use conservative defaults or hardware-specific logic where appropriate.

---

## Failure 12 — Treating browser/profile state like ordinary dotfiles
### What goes wrong
A seeded app profile carries caches, IDs, or fragile state.

### Symptom
Privacy issues, stale state, brittle upgrades, strange app behavior.

### The real fix
Be extremely cautious with app-profile seeding, especially browsers.

---

# 28. The clean maintenance model for rebuilding every few weeks

Your rebuild-every-few-weeks plan is exactly the right mindset for Arch.

A rolling-release offline ISO should be treated as a sequence of release snapshots.

---

## 28.1 Each rebuild should be treated as a new release
For each release cycle, conceptually refresh:

- official package snapshot
- AUR build outputs
- custom package outputs
- trust/keyring state
- dotfiles payload commit
- installer logic revision
- release manifests/checksums

This is not “busywork.”
This is how a rolling offline installer stays healthy.

---

## 28.2 A good release checklist mindset
Each rebuild should answer:

### Package questions
- Did the official package set change?
- Did any dependencies move?
- Do any AUR packages need rebuilds against the new snapshot?
- Did any custom package payloads change?

### Trust questions
- Is the keyring current enough?
- Does the trust model still match the repo contents?
- If using a persistent custom repo, is target trust still correct?

### Dotfiles questions
- What commit is being shipped?
- Did any new config introduce hardware-specific assumptions?
- Are executable bits and file classifications still correct?

### Installer questions
- Did any script logic change?
- Does the installer still create users only after target `/etc/skel` is ready?
- Is target repo policy still valid?

### Runtime questions
- Does first boot still produce a working session?
- Are services and session components activating correctly?
- Do shared assets resolve correctly from system paths?

---

## 28.3 Rebuild cadence and release hygiene
Because Arch is rolling, a practical release discipline usually includes:
- rebuilding regularly
- testing the fresh ISO
- publishing a new checksum/signature set
- retiring older images after some reasonable window
- documenting what changed

The older an offline ISO gets, the greater the chance of:
- stale keyrings
- outdated packages
- broken trust assumptions
- rougher post-install update transitions

---

# 29. A sensible testing strategy

If this ISO will be used by other people, testing is not optional.

You need to test more than just “it boots once.”

---

## 29.1 Test the build artifact itself
At minimum:
- does the ISO boot on intended UEFI systems?
- does the live installer start correctly?
- does the local repo exist and resolve packages correctly?
- do trust checks succeed offline?

---

## 29.2 Test the installation flow
At minimum:
- can a full target installation complete without network?
- are target root and boot partitions populated correctly?
- is the installed disk bootable without the USB?
- is the user created at the right stage?
- does the created user receive the intended `/etc/skel` contents?

---

## 29.3 Test the first-boot runtime
At minimum:
- does the installed system boot?
- does the login/session path work?
- does Hyprland launch?
- do user configs load correctly?
- do shared assets resolve correctly?
- do basic desktop components behave as expected?

---

## 29.4 Test on the hardware classes you claim to support
If you claim broader support, test broader support.

At minimum, your stated support policy should match your actual testing.

---

# 30. The deepest single operational insight

If there is one operational sentence that captures the whole project, it is this:

> A fully offline custom Arch ISO is a release snapshot that combines a bootable live installer environment, a self-contained local `pacman` repository, machine-wide target policy, shared target assets, and target user-default payloads, all arranged so the target system is built from packages offline, configured as a real Arch installation, and only then used to create the final user from the target system’s own `/etc/skel`.

That is the real design.

---

# 31. The fully corrected end-to-end story in one continuous narrative

To tie everything together, here is the whole lifecycle in one uninterrupted flow.

---

## 31.1 In the Factory
You define the target system:
- base OS
- hardware support
- Hyprland stack
- desktop integration stack
- apps
- shared assets
- machine policy
- user defaults

You gather official packages as one coherent snapshot.

You build needed AUR packages cleanly.

You build your custom packages:
- machine-policy payload
- shared-assets payload
- user-defaults payload

You export your dotfiles from a specific Git commit into a clean checked-out file tree.

You ensure only true per-user defaults go into the `/etc/skel` payload.

You prepare trust/keyring state current enough for the package snapshot.

You build the live installer environment.

You place the local repo on the ISO.

You assemble the ISO and release metadata.

Now you have a versioned installer artifact.

---

## 31.2 On the user’s machine: boot installer medium
The machine boots the installation medium.

The live installer environment starts.

This environment is temporary and separate from the final installed system.

It contains the tools and trust state needed to perform installation offline.

---

## 31.3 On the user’s machine: prepare target disk
Your installer scripts:
- partition the disk
- create filesystems
- mount the target root
- mount target boot/EFI partitions correctly

At this point, the target system is only an empty mounted root tree waiting to be populated.

---

## 31.4 On the user’s machine: install packages offline
The live installer points `pacman` / `pacstrap` at the local repo on the installation medium.

The package manager:
- reads repo metadata
- resolves dependencies
- verifies packages offline
- installs packages into the target root
- populates the target package database

Now the target system receives:
- base OS
- kernel/firmware
- graphics stack
- Hyprland stack
- apps
- machine policy
- shared assets
- user defaults

---

## 31.5 On the user’s machine: finalize target configuration
The installer writes machine-specific config into the target system.

This includes:
- target `/etc` machine policy
- valid post-install `pacman` repo policy
- login/session behavior
- service settings
- any remaining boot policy or host configuration

---

## 31.6 On the user’s machine: create the user
Only after target `/etc/skel` is ready do you create the user.

The account-creation logic copies target `/etc/skel` into the new home.

Ownership becomes user-owned.

Now the user has:
- Hyprland config
- shell config
- bar config
- launcher config
- helper scripts
- other per-user defaults

Shared assets remain in shared system locations.

Machine policy remains in `/etc`.

---

## 31.7 On the user’s machine: finalize target bootability
The installer completes target boot setup.

The target disk is now independently bootable.

---

## 31.8 First boot into the installed system
The machine boots from its own disk.

The target boot path starts the installed system.

Machine boot services and machine policy come up first.

A login/session path becomes available.

The user session is created.

User-session components activate through the mechanisms your design uses.

Hyprland runs and reads config from the user’s home.

Waybar, launcher, notifications, and related user tools read from the user’s config.

Wallpapers, fonts, themes, icons, and other shared assets are loaded from shared package-managed locations.

Now the user is inside the installed desktop.

That is the complete pipeline.

---

# 32. Final condensed summary of all three parts

If you want the shortest correct summary of the whole guide, it is this:

1. **Build the ISO in a Factory environment that is allowed to use the internet.**
2. **Treat each ISO as a coherent release snapshot.**
3. **Use a real local `pacman` repository on the ISO, not just a package cache.**
4. **Prebuild AUR software into binary packages in the Factory.**
5. **Separate machine policy, shared assets, and user defaults into different payloads.**
6. **Put machine policy in target `/etc`.**
7. **Put shared resources in target `/usr/share` or related package-managed shared locations.**
8. **Put default user config in target `/etc/skel`.**
9. **Populate target `/etc/skel` before creating the target user.**
10. **Design runtime activation explicitly: boot services, login/session flow, user-session components, and compositor startup are not all the same thing.**
11. **Make post-install repo policy explicit and valid.**
12. **Treat trust/keyring state as part of every release.**
13. **Test the ISO, the install path, and the first-boot runtime, not just one of them.**

---

If you want, the next step can be one of these:

1. I can turn all 3 parts into **one polished, deduplicated master document** formatted cleanly for Obsidian, or  
2. I can turn this corrected theory into a **practical implementation roadmap** in order, from “what do I create first?” to “how do I assemble the final ISO?”
