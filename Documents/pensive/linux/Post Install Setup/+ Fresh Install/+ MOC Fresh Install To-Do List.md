# Fresh Arch Linux Installation Checklist

This checklist provides a structured overview of essential tasks to perform after a fresh Arch Linux installation. Follow these steps to configure your system, restore your environment, and set up your applications.

> [!NOTE] Sections with "*Script*" at the top indicate that section has been automated with a script at the following location.  
> ```bash
> cd ~/user_scripts/arch_setup_scripts/scripts/
> ```

> [!IMPORTANT]+
> Steps to be followed Sequentially. 
> there are going to be a few errorw displayed on the top of the screen by hyprland, ignore those, they will eventually go away as each step is followed to the T

### 1. Core System & Environment Setup

This phase focuses on critical system files, user environment, and restoring your base configuration.

- [ ] **Login with uwsm** 

```bash
exec uwsm start hyprland
```

- [ ] **Connect to the internet**: Depending on what you use, (ie tethering does not usually need to be setup) [[Network Manager]]

---

- [ ] *Optional but recommended* There are 2 commands that are long and complex, to prevent typos, it's recommended to copy paste them by SSH'ing into the PC from a phone or another pc, for referenced- not needed to refer to [[SSH]]

```bash
sudo systemctl start sshd && ip a
```

---
*Script*
**OPTIONAL**
- [ ] limit battery temperately (asus tuf f15) (might need to change `BAT1`to see what's available for your laptop for this command to work)

```bash
echo 60 | sudo tee /sys/class/power_supply/BAT1/charge_control_end_threshold
```

---

- [ ] **Restore Dotfiles:** Download the `git` bare repository and deploy the files on your PC [[Restore Backup On a Fresh Install]].

**OPTIONAL**
>[!error]+ Your mouse button will be reversed AFTER RESTORING THE DOTFILES, if you're using a physical mouse. 
>Trackpad not effected. You'll later have the option to set it back to normal configuration in the steps below, or you could do it right now in the following file
>```bash
>nvim ~/.config/hypr/source/input.conf
>```
> change`left_handed = true` to `left_handed = false`

---

**OPTIONAL**
- [ ] If you want to quickly find out how to do something using an existing keybind, hold down `ALT` + `6` for the rofi menu to list the keybind list

---

- [ ] **Link Restored Vault files to Obsidian** : open and link to existing vault. 
- When you open Obsidian for the first time, you'll be prompted with three options. Select "open Folder as Vault (Choose an existing folder of Markdown Files)" this directory should have been populated in ~/Documents/pensive/ after you restored the git files. 

- make sure to NOT create a new vault or sync. select the aforementioned option and navigate to the pensive directory to be selected as source for existing markdown files. 

- You can then copy paste commands from Obsidian on the same PC, no SSHing required

---

- [ ] **DNS configuration**
symbolic link so systemd resolved gets used instead of the isp. 

```bash
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
```

[[Systemd Resolve Config]]

---
*Script*
- [ ] **Set up GNOME Keyring:** Configure GNOME Keyring with PAM for password management. [[Gnome Keyring PAM]]

---
*Script*
- [ ] **Enable UserSession Services**. 

```bash
systemctl --user enable --now pipewire.socket pipewire-pulse.socket wireplumber.service hypridle.service hyprpolkitagent.service fumon.service gnome-keyring-daemon.service gnome-keyring-daemon.socket hyprsunset
```

---
*Script*
- [ ] configure Reflector config. 
```bash
sudo nvim /etc/xdg/reflector/reflector.conf
```

replace the entire content of the file with this. 
```ini
--save /etc/pacman.d/mirrorlist

# Select the transfer protocol (--protocol).
--protocol https

# Select the country (--country).
# Consult the list of available countries with "reflector --list-countries" and
# select the countries nearest to you or the ones that you trust. For example:
--country India

# Use only the  most recently synchronized mirrors (--latest).
--latest 6

# Sort the mirrors by synchronization time (--sort).
--sort rate 
```

**OPTIONAL**
- [ ] Syncing Pacman Mirrors for faster Download Speeds

if your downloads are currently slow, run this. (**Though Usually not needed**)
```bash
sudo reflector --protocol https --country India --latest 6 --sort rate --save /etc/pacman.d/mirrorlist
```

---
*Script*
- [ ] **pacman-keyring** You must run `pacman-key --init` before first using pacman; the local
keyring can then be populated with the keys of all official Arch Linux packagers with `pacman-key --populate archlinux`.

```bash
sudo pacman-key --init
```

```bash
sudo pacman-key --populate archlinux
```


---
*Script*
**OPTIONAL**
- [ ] enabling better pacman visuals while downloading packages and faster downloads
```bash
sudo nvim /etc/pacman.conf
```

replace the entire content of the file with this. 

> [!NOTE]- copy all of it. 
> ```
> # /etc/pacman.conf
> # See the pacman.conf(5) manpage for option and repository directives
> [options]
> # The following paths are commented out with their default values listed.
> # If you wish to use different paths, uncomment and update the paths.
> 
> # Pacman won't upgrade packages listed in IgnorePkg and members of IgnoreGroup
> #IgnorePkg   =
> #IgnoreGroup =
> 
> #NoUpgrade   =
> #NoExtract   =
> 
> # Misc options
> Color
> ILoveCandy
> VerbosePkgLists
> HoldPkg     = pacman glibc
> Architecture = auto
> CheckSpace
> ParallelDownloads = 5
> DownloadUser = alpm
> 
> # By default, pacman accepts packages signed by keys that its local keyring
> # trusts (see pacman-key and its man page), as well as unsigned packages.
> SigLevel    = Required DatabaseOptional
> LocalFileSigLevel = Optional
> #RemoteFileSigLevel = Required
> 
> # NOTE: You must run `pacman-key --init` before first using pacman; the local
> # keyring can then be populated with the keys of all official Arch Linux
> # packagers with `pacman-key --populate archlinux`.
> 
> #
> # REPOSITORIES
> #   - can be defined here or included from another file
> #   - pacman will search repositories in the order defined here
> #   - local/custom mirrors can be added here or in separate files
> #   - repositories listed first will take precedence when packages
> #     have identical names, regardless of version number
> #   - URLs will have $repo replaced by the name of the current repo
> #   - URLs will have $arch replaced by the name of the architecture
> #
> # Repository entries are of the format:
> #       [repo-name]
> #       Server = ServerName
> #       Include = IncludePath
> #
> # The header [repo-name] is crucial - it must be present and
> # uncommented to enable the repo.
> #
> 
> # The testing repositories are disabled by default. To enable, uncomment the
> # repo name header and Include lines. You can add preferred servers immediately
> # after the header, and they will be used before the default mirrors.
> 
> #[core-testing]
> #Include = /etc/pacman.d/mirrorlist
> 
> [core]
> Include = /etc/pacman.d/mirrorlist
> 
> #[extra-testing]
> #Include = /etc/pacman.d/mirrorlist
> 
> [extra]
> Include = /etc/pacman.d/mirrorlist
> 
> # If you want to run 32 bit applications on your x86_64 system,
> # enable the multilib repositories as required here.
> 
> #[multilib-testing]
> #Include = /etc/pacman.d/mirrorlist
> 
> [multilib]
> Include = /etc/pacman.d/mirrorlist
> 
> # An example of a custom package repository.  See the pacman manpage for
> # tips on creating your own repositories.
> #[custom]
> #SigLevel = Optional TrustAll
> #Server = file:///home/custompkgs
> ```

---
*Script*
- [ ] **Set Default Shell:** Change the default shell from `bash` to `zsh` 
- To make Zsh your login shell, use the `chsh` (change shell) command and then enter your Password

```bash
chsh -s $(which zsh)
```

> [!IMPORTANT]-
> For the change to take full effect, you must **log out and log back in**. Simply opening a new terminal window is not enough.

- [ ] **Reboot** :

```bash
systemctl reboot
```

After logging back in, you can verify that your shell has been changed:

```bash
echo $SHELL
```

The output should be `/bin/zsh` or `/usr/bin/zsh`.

---
*Script*
- [ ] **Install `paru`:** Set up the `paru` AUR helper. [[Installing an AUR Helper]]

---
*Script*
**OPTIONAL** (Recommanded to circumvent geo blocking of aur url's by the ISP) 
- [ ] **Install Warp and connect to it** or some packages might download excruciatingly slowly [[Warp Cloudflare]]

---
*Script*
**OPTIONAL**
- [ ] Arch extra repo has a history of messing up the packaging for the plugins with hyprland resulting in mismatched headers leading to errors. Enable/disable plugins entirely. [[Toggling Hypr Plugins Manager]]

---
*Script*
**OPTIONAL** but Recommanded
Run this only if you have the plugins enabled and want to use them. 

- [ ] run this once to install and enable hyprland plugins- hyprpm

```bash
hyprpm update
```

- [ ] add this hyprland plugins repo to install from a list of plugins. 

```bash
hyprpm add https://github.com/hyprwm/hyprland-plugins
```

- [ ] enable `hyprexpo` plugin for overview preview of workspaces

```bash
hyprpm enable hyprexpo
```

---
*Script*
- [ ] **Install Core Applications:** Use `paru` to install your essential packages from the repositories and the AUR. [[AUR Packages]]

---
*Script*
- [ ] **Enable Aur packages' services** [[AUR Package services]]

```bash
sudo systemctl enable --now fwupd.service warp-svc.service asusd.service preload
```

---
*Script*
- [ ] **Create Directories** for Block device mount points. (only create the ones you have drives for)

```bash
sudo mkdir /mnt/{browser,windows,wdslow,wdfast,media,fast,slow,enclosure}
```

---
*Script*
- [ ] Preload config. [[Preload Setup]]

---
*Script
- [ ] Nvim's plugins download : 
```bash
nvim --headless "+Lazy! sync" +qa
```

if for some reason neovim's plugns were corrupted , you can reset nvim by deleting these drectorys and then running the downlaod command again. 

```bash
rm -rf ~/.config/nvim/lazy-lock.json ~/.local/share/nvim ~/.local/state/nvim ~/.cache/nvim
```


---
*Script*
**OPTIONAL**
- [ ] **Update `fstab`:** Edit the fstab to reflect the new drives' UUIDs. **fstab requires unlocked UUIDs of block devices** [[fstab reference]] 
- find out UUID's of your relevant disks. boot, home & root are already set. don't touch those in fstab. 

```bash
lsblk -f
```
or 
```bash
sudo blkid
```
or 
```bash
lsblk -o NAME,MODEL,TYPE,SIZE,MOUNTPOINT,FSTYPE,FSVER,UUID,STATE
```

then :
```bash 
sudo nvim /etc/fstab
```

- After making changes to the fstab file, make sure to reload the file into memory. 

```bash
sudo systemctl daemon-reload
```

---
*Script*
**OPTIONAL**
- [ ] **Update Drive Unlock Script:** Change the UUID in your LUKS/drive unlocking script. **Both, lock and unlock scripts require Locked UUIDs**

- Test if it worked by running the unlock drive script for browser drive. There's an alias for it in the zshrc file, run this. and enter your password, Then check if it correctly mounted at /mnt/browser/

```bash
unlock browser
```

---
*Script*
**OPTIONAL**
- [ ] **Clipboard Persistent/Ephemeral** (Default is Ephemeral)
      if you want Your clipboard contents to persistent through reboots/shutdowns, comment out the line in :

```bash
nvim .config/uwsm/env
```

Comment out this line:

>[!note] export CLIPHIST_DB_PATH="${XDG_RUNTIME_DIR}/cliphist.db"


---
*Script*
- [ ] **Create a symlink** for the service file in user_scripts/waybar/network so the service works. , this is done because service files are looked for in .config/systemd/user/.

```bash
ln -nfs $HOME/user_scripts/waybar/network/network_meter.service ~/.config/systemd/user/network_meter.service
```

and then enable the service 
```bash
systemctl --user enable --now network_meter
```

---
*Script*
- [ ] **Create a symlink** for the service file in user_scripts/battery/battery_notify.service, so the service works. , this is done because service files are looked for in .config/systemd/user/.

```bash
ln -nfs $HOME/user_scripts/battery/notify/battery_notify.service ~/.config/systemd/user/
```

and then enable the service 
```bash
systemctl --user enable --now battery_notify
```

---
*Script*
-[ ] **bibata-modern-classic** theme for cursor
```bash
# 1. Create directory
mkdir -p ~/.local/share/icons

# 2. Download & Extract (Pipe)
curl -L https://github.com/ful1e5/Bibata_Cursor/releases/download/v2.0.7/Bibata-Modern-Classic.tar.xz | tar -xJ -C ~/.local/share/icons/

# 3. Apply to Hyprland
hyprctl setcursor Bibata-Modern-Classic 18
```

```bash
# 4. (Optional) Fix Legacy Apps
mkdir -p ~/.local/share/icons/default
echo -e "[Icon Theme]\nName=Default\nInherits=Bibata-Modern-Classic" > ~/.local/share/icons/default/index.theme
```

---

(NO LONGER NEEDED, MOVED TO USER CONFIG , SKIP TO NEXT) 
*Script* 
- [ ] Preferred system and terminal fonts.  if you want you could refer to this note for more info [[+ MOC Fonts]] but reading it is not needed, just follow the steps below. 

- **Copy the Pre Configured Configuration file to the  system fonts directory**

```bash
sudo cp ~/fonts_and_old_stuff/setup/etc/fonts/local.conf /etc/fonts/
```

- Refresh the fonts. 

```bash 
sudo fc-cache -fv
```

---
*Script*
## Theming (matugen)

- [ ] First create the following directories for gtk4, btop, wal for firefox. 

```bash
mkdir -p $HOME/.config/gtk-4.0 && mkdir -p $HOME/.config/btop/themes && mkdir -p $HOME/.cache/wal
```

---
*Script*
**Optional**
- [ ] You can place pictures for the wallpaper selector in the wallpapers directory at:- 

place an image in:
```bash
cd $HOME/Pictures/wallpapers/
```

- [ ] **Only for me on asus tuf  f15:** Copy the existing wallpapers folder from the backup media drive and into the local pictures directory 

```bash
cp -r /mnt/media/Documents/do_not_delete_linux/wallpapers ~/Pictures/
```

---
*Script*
- [ ] Apply a wallpaper. (multiple ways) Option A recommanded. 

- option a: Keybind **Super** + **apostrophe(')**

- option b: with waypaper :- just open waypaper from rofi or terminal and select the wallpapers directory and select any image, or open waypaper with the keybind `Alt + 4` and pick your wallpaper

- option c :or run this command to have matugen generate the colors and place them in required direcotries for the errors to go away 
this command picks an image at random and generates a color pallette for it. 
```bash
matugen image "$(find "$HOME/Pictures/wallpapers" -type f | shuf -n 1)"
```

or pick a specific image manually 
```bash
matugen image $HOME/Pictures/Wallpapers/image.jpg
```

---
*Script*
`Check carefully before changing the following qtct files, these might already be as they should becase they should have been restored from github but sometimes the lines change on a fresh install. And if this step needs to be carried out, only change the lines specified below and nothing else. leave everything as is.`

- [ ] **for qt5ct**  Theming with matugen
open this file

```bash
nvim ~/.config/qt5ct/qt5ct.conf
```

replace these lines at the top of the file with this

```bash
[Appearance]
color_scheme_path=$HOME/.config/matugen/generated/qt5ct-colors.conf
custom_palette=true
standard_dialogs=default
style=Fusion
```

- [ ] **for qt6ct**  Theming with matugen
open this file

```bash
nvim ~/.config/qt6ct/qt6ct.conf
```

replace these lines at the top of the file with this

```bash
[Appearance]
color_scheme_path=$HOME/.config/matugen/generated/qt6ct-colors.conf
custom_palette=true
standard_dialogs=xdgdesktopportal
style=Fusion
```

if qt apps still aren't follwing the color pallete of matugen. *sometimes you might need to open `qt5ct` and `qt6ct` and mess around with its settings*

---
*Script*
- [ ] **Gtk3/Gtk4 root symlink**

```bash
# 1. Get your home directory reliably
USER_HOME=$(getent passwd "$USER" | cut -d: -f6)

# 2. Ensure root config directory exists
sudo mkdir -p /root/.config

# 3. Delete existing root GTK folders (Clean slate)
sudo rm -rf /root/.config/gtk-3.0 /root/.config/gtk-4.0

# 4. Create the Symlinks (User -> Root)
# -s: symbolic, -f: force, -T: no-target-directory (prevents nesting)
sudo ln -sfT "$USER_HOME/.config/gtk-3.0" /root/.config/gtk-3.0
sudo ln -sfT "$USER_HOME/.config/gtk-4.0" /root/.config/gtk-4.0
```

---
*Script*
` This step, again, is usually not needed to be done but check if its needed, by setting a wallpaper with waypaper and see if the theme has switched, open and close the terminal/thunar/or anyother app,  to see if it's switched colors, if not, then preceed with the following:` if themes did switch sucessfuly, this step is not required. 

- [ ] Might need to recreate the config file for waypaper because sometimes it's got issues when it's restored from git. so delete the entire file, open waypaper> change any setting> when a new config is auto created, edit it just the post_command line to include the command. 

```bash
rm ~/.config/waypaper/config.ini && waypaper
```

```bash
nvim ~/.config/waypaper/config.ini
```

```ini
post_command = matugen --mode dark image $wallpaper
```

> [!NOTE]- Contents
> ```ini
> [Settings]
> language = en
> folder = ~/Pictures/wallpapers
> monitors = All
> wallpaper = ~/Pictures/wallpapers/dusk_default.jpg
> show_path_in_tooltip = True
> backend = swww
> fill = fill
> sort = name
> color = #ffffff
> subfolders = False
> all_subfolders = False
> show_hidden = False
> show_gifs_only = False
> zen_mode = False
> post_command = matugen --mode dark image $wallpaper
> number_of_columns = 3
> swww_transition_type = any
> swww_transition_step = 63
> swww_transition_angle = 0
> swww_transition_duration = 2
> swww_transition_fps = 60
> mpvpaper_sound = False
> mpvpaper_options = 
> use_xdg_state = False
> stylesheet = /home/dusk/.config/waypaper/style.css
> ```

--- 
*Script*
**OPTIONAL**
DARK/LIGHT THEME SWITCH

- [ ] to change the color scheme from dark to light or the other way around. 
you can left/right click the color theme toggle on the waybar. (might need to click it multiple times for theme to switch or just click it once and then apply a wallpaper with waypaper or `super + apostrophe(')`) , If waybar is not toggled, you can open it with `Alt + 9` and close it with `Alt + 0`

## or
	(not recommended because this is not persistent and it doesnt change it for matugen colors)
- [ ] manually open nwg-loog and set the `color scheme` to either `prefer dark` or `prefer light` if it doesn't apply automatically when switching wallpaper and triggering the matugen command

---

**OPTIONAL**

- [ ] Obsidian themeing (matugen)
Obsidian doesn't usually respect matugen theming on its own so you need to manually do two things. 
first, make sure you've set the appropriate colro scheme for your current theme - dark/light from the waybar. 

then open obsidian's settings > Appearance > Scroll to the bottom to `CSS snippets`> Toggle on the `matugen-theme` option, and not the other one (if it exists). if you toggle on both, sometimes they will both be toggled off the next time you open Obsidian

---
*Script*
- [ ] Set over all animations with just one click. 
hold down `ALT` + `SUPER(windows key)` + `A`  after you hold down all these at once, rofi menu will show up for you to select from a list of animations. pick anyone. (`fluid` is recommended but pick the one you like.)

---
*Script*
- [ ] **Sound notify when usb plugin/disconnect** [[Configure udev auditory notify usb plugin]]

---
*Script*
- [ ] Set kitty as your defualt terminal. 
```bash
nvim ~/.config/xdg-terminals.list
```
and then paste this in it. just this. 
```ini
kitty.desktop
```

---
*Script*
**OPTIONAL**
- [ ] block attention sucking sites. 

```bash
sudo nvim /etc/hosts
```

> [!NOTE]- Hosts file blocking
> ```ini
> # Static table lookup for hostnames.
> # See hosts(5) for details.
> 127.0.0.1        localhost
> ::1              localhost
> 0.0.0.0 instagram.com
> 0.0.0.0 www.instagram.com
> 0.0.0.0 facebook.com
> 0.0.0.0 www.facebook.com
> 0.0.0.0 m.facebook.com
> 0.0.0.0 x.com
> 0.0.0.0 www.x.com
> 0.0.0.0 twitter.com
> 0.0.0.0 www.twitter.com
> 0.0.0.0 twitch.tv
> 0.0.0.0 www.twitch.tv
> 0.0.0.0 kick.com
> 0.0.0.0 www.kick.com
> 0.0.0.0 www.reddit.com
> ```

---
*Script*
- [ ] Make sure the user name for your desktop entries in this direcotry matches your username.
```bash
cd ~/.local/share/applications/
```

> [!NOTE]- files to check, if your username is anything other than dusk, change them, example for this line in each file "Exec=uwsm-app -- /home/dusk/user_scripts/sliders/brightness_slider.sh" change dusk to what every your username is. 
> ```ini
>     "asus_control.desktop"
>     "brightness_slider.desktop"
>     "cache_purge.desktop"
>     "clipboard_persistance.desktop"
>     "file_switcher.desktop"
>     "hypridle_timeout.desktop"
>     "hypridle_toggle.desktop"
>     "hyprsunset_slider.desktop"
>     "IO_Monitor.desktop"
>     "matugen.desktop"
>     "mouse_button_reverse.desktop"
>     "new_github_repo.desktop"
>     "opacity_blur_shadow.desktop"
>     "openssh.desktop"
>     "powersave.desktop"
>     "process_terminator.desktop"
>     "relink_github_repo.desktop"
>     "rotate_screen_clockwise.desktop"
>     "rotate_screen_counter_clockwise.desktop"
>     "scale_down.desktop"
>     "scale_up.desktop"
>     "sysbench_benchmark.desktop"
>     "tailscale_setup.desktop"
>     "tailscale_uninstall.desktop"
>     "volume_slider.desktop"
>     "warp.desktop"
>     "waybar_config_switcher.desktop"
>     "wifi_security.desktop"
> ```

---
*Script*
**OPTIONAL** 
- [ ] *Optional* : Link Browser data to existing drive (only do if you have a separate browser drive where you want for browser data to be stored)

- Do Not Open Firefox until all steps are done (close it if it's open)

- First Completely Wipe Firefox Data on your current setup. 

- Removes the primary Firefox profile data
- Removes the parent .mozilla directory, catching all related data
- Clears the application cache for Firefox

```bash
rm -rf ~/.mozilla/firefox/ ~/.mozilla ~/.cache/mozilla
```

create the .mozilla directory that will then be simlinked (make sure the drive is mounted and created first)

```bash
mkdir -p /mnt/browser/.mozilla
```

This command links the `.mozilla` folder from an external drive mounted at `/mnt/browser/.mozilla` to the location where Firefox expects to find it in the user's home directory.

```bash
sudo ln -nfs /mnt/browser/.mozilla ~/.mozilla
```


---

**OPTIONAL**
**DONT RUN THIS** if you've linked your browser to another partition (only do it if it's configured in a usually way, default. )
- [ ] **Firefox cache to ram** with profile-sync-daemon. 

```bash
sudo pacman -S profile-sync-daemon
```
 
```bash
systemctl --user enable psd.service
```

---

**OPTIONAL**
- [ ] Firefox themeing (if you use firefox.) 
install the extention `Pywalfox` from the mozilla store. and then open the plugin and select `Fetch Pywal colors`

---

**OPTIONAL**

- [ ] **Comment out anything beyond  the end line of zshrc, if there is anything there,  to speed up your terminal:** :- 

```bash
nvim ~/.zshrc
```

>[!note]- Comment out beyond this part
> ===========================
> End of ~/.zshrc
> ============================

---
*Script*
- [ ] *Optional*: **TLP config** : copy the tlp config to /etc/tlp.conf [[+ MOC tlp config]]

---

- [ ] *Optional*:**Create Disk Swap** [[Disk Swap]] 
      zram swap should already have been created during installation process, you can check if zram block drives are active. usually zram0 and zram 1 if you followed the instruction during arch install. 

	If you Still want more swap and can spare some disk storage for it, you can create disk swap, it's recommanded to create one if you have =<4gb of ram. 

---
*Script*
- [ ] *Optional*: **Configure Auto-Login:** Set up automatic login on TTY1. [[+ MOC Auto Login]]

---
*Script*
- [ ] *Optional*:**Configure swapiness for zram** Optimal if you have sufficiant ram ie equal to or more than 4GB [[Optimizing Kernel Parameters for ZRAM]]

---
*Script*
- [ ] *Optional*: **Configure Power Key:** Define the system's behavior when the power key is pressed. [[Power Key Behaviour]]

---
*Script*
**OPTIONAL**
- [ ] Fix logratate by uncommenting size and compress in 

```bash
sudo nvim /etc/logrotate.conf
```

or replace the entire content of the wile with this 

> [!NOTE]- replace with this. 
> ```ini
> # see "man logrotate" for details
> # rotate log files weekly
> weekly
> 
> # keep 4 weeks worth of backlogs
> rotate 4
> 
> # restrict maximum size of log files
> size 20M
> 
> # create new (empty) log files after rotating old ones
> create
> 
> # uncomment this if you want your log files compressed
> compress
> 
> # Logs are moved into directory for rotation
> # olddir /var/log/archive
> 
> # Ignore pacman saved files
> tabooext + .pacorig .pacnew .pacsave
> 
> # Arch packages drop log rotation information into this directory
> include /etc/logrotate.d
> 
> /var/log/wtmp {
>     monthly
>     create 0664 root utmp
>     minsize 1M
>     rotate 1
> }
> 
> /var/log/btmp {
>     missingok
>     monthly
>     create 0600 root utmp
>     rotate 1
> }
> ```

---
*Script*
**OPTIONAL**
- [ ] fix being locked out if you enter incorrect password [[Incorrect Password Attempt Timeout]]

---

**OPTIONAL**
- [ ] Run Jdownloader once to let it downlaod all the files it needs to update itself. 
`just open it with rofi or wofi` and click yes if there's an update. 

---

- [ ] **Reboot** 

```bash
systemctl reboot
```

---

### 2. Very Important to REMOVE the following configs if you have a PC other than Asus Tuf f15 2022 or a pc without a Dedicated GPU like NVIDIA or AMD

Fine-tune your Hyprland compositor and shell environment. These steps are often machine-specific.

> [!CAUTION]+
> The following steps involve hardware-specific settings. Adjust them carefully based on whether you are on Asus tuf 15 or another pc  and what GPU you are using.

## For non Asus tuf f15 laptops

**Clean Environment Variables:**

- [ ] Comment OUT any and all environment variable under the Nvidia section in the uwsm env file. 

```bash
nvim ~/.config/uwsm/env
```

> [!error]+ Comment OUT Everything Beyond This Line
>\#-------------------------NVIDIA-------------------------------
>\#COMMENT OUT ANY SET ENVIRONMENT VARIABLE IF YOU DONT HAVE NVIDIA
>\#--------------------------------------------------------------

also comment out this line 

> [!error]+ put a hash before it. 
>```ini
>export LIBVA_DRIVER_NAME=iHD
>```

---

- [ ] Comment OUT this variable If you only have integrated GPU i.e no NVIDIA

```bash
nvim ~/.config/uwsm/env-hyprland
```

> [!error]+ Comment OUT this line
>```ini
>export AQ_DRM_DEVICES=/dev/dri/card1
>```

---
*Script*
**HYPRLAND CONFIG** changes. 

```bash
nvim ~/.config/hypr/hyprland.conf
```

 - [ ] This line is to run a script for configuring asus profiles for Asus specific hardware and for changing keyboard color along with fan control. 
 
 ```bash
 nvim .config/hypr/source/keybinds.conf
 ```


>[!error]+ Comment out Asus specific Script
>```ini
>binddl = , XF86Launch3, ASUS Control, exec, uwsm-app -- $terminal --class asusctl.sh -e sudo $scripts/asus/asusctl.sh
>```

- [ ] Comment OUT the custom key-binds for changing refresh rate that are specific to asus laptops with 144 hz with  `Alt+6` and `Alt+7`.

> [!error]+ Comment OUT these two lines
>```ini
> bindd = ALT, 7, Set Refresh rate to 48Hz Asus Tuf, exec, hyprctl keyword monitor eDP-1,1920x1080@48,0x0,1.6 && sleep 2 && hyprctl keyword misc:vrr 0
>bindd = ALT, 8, Set Refresh rate to 144Hz Asus Tuf, exec, hyprctl keyword monitor eDP-1,1920x1080@144,0x0,1.6 && sleep 2 && hyprctl keyword misc:vrr 1
>```

- [ ] IF you dont plan on using tts/stt, comment out these lines. 

> [!error]+ comment them out if you dont plan on using the tts/stt
> ```ini
> bindd = $mainMod, O, TTS Kokoro GPU, exec, wl-copy "$(wl-paste -p)" && uwsm-app -- $scripts/kokoro_gpu/speak.sh
> bindd = $mainMod SHIFT, O, TTS Kokoro CPU, exec, wl-copy "$(wl-paste -p)" && uwsm-app -- $scripts/kokoro_cpu/kokoro.sh
> 
> # FasterWhisper STT
> bindd = $mainMod SHIFT, I, STT Whisper CPU, exec, uwsm-app -- $scripts/faster_whisper/faster_whisper_stt.sh
> 
> # NVIDIA Parakeet
> bindd = $mainMod, I, STT Parakeet GPU, exec, uwsm-app -- $scripts/parakeet/parakeet.sh
> ```


---

- [ ] **Configure Monitor Output:** Here one line needs to be Un-Commented and another Commented out. 

```bash
nvim .config/hypr/source/monitors.conf
```

> [!tip]+ Un-Comment this line to auto detect Your Screen configuration
> #monitor=,preferred,auto,auto  # Generic rule for most laptops

> [!error]+ Comment OUT this line (specifically for 144 hz asus laptop)
> monitor=eDP-1,1920x1080@60,0x0,1.6 # Specific for ASUS TUF F15 Laptop

---
*Script*
- [ ] Mouse left/right click buttons are swapped by default, switch them back to normal. 

```bash
nvim .config/hypr/source/input.conf
```

> [!error]+ Comment OUT this line 
>```ini
> left_handed = true
>```

---
- [ ] Remove clipboard pins and errands, that are specifically for dusk's personal use. 
```bash
rm -f "${HOME}/.local/share/errands/data.json"
rm -rf "${HOME}/.local/share/rofi-cliphist/pins"
```
---
*Script*
- [ ] for changing default file manager from yazi to thunar. 

> [!tip]- to change yazi to thunar as default
> open this file
> ```bash
> nvim .config/hypr/source/keybinds.conf
> ```
>replace this line `$fileManager = yazi` with 
>```ini
>$fileManager = thunar
>```
>and then replace this line `bindd = $mainMod, E, File Manager, exec, uwsm-app -- $terminal -e $fileManager` with 
>```ini
>bindd = $mainMod, E, File Manager, exec, uwsm-app $fileManager
>```
>and then finally run this command
>```bash
>xdg-mime default thunar.desktop inode/directory
>```
> explination of the command
> By running this command, you are telling your system, "From now on, whenever you are asked to 'open' a directory, use the application defined in thunar.desktop." This change is saved specifically for your user in the ~/.config/mimeapps.list file.
> xdg-mime default: This is the command to set a default application.
>thunar.desktop: This is the standard desktop entry for the Thunar application. The system looks for this file in /usr/share/applications/ to get information about how to run Thunar.
>inode/directory: This is the official MIME type for a folder or directory.

---

Dont do this, reserach what your specific gpu has.  (skip this)
- [ ] Comment out this line from mpv's config if you don't have an av1 decoder. can be checked by running `vainfo`

```bash
nvim ~/.config/mpv/mpv.conf
```

> [!error]+ Comment out this part
> hwdec-codecs=vp9,h264,hevc,av1


---
*Script*
- [ ] On hybrid laptop setups (dGPU + iGPU), `swaync` (Sway Notification Center) can default to binding to the dGPU. This prevents the discrete card from suspending (D3cold state), leading to significantly higher power draw. To fix this, a systemd drop-in override is often used to force the iGPU. However, if you are strictly on a single GPU setup or need to disable this fix for debugging, you should disable the override file. 
Method: Safe Disable (Rename) Instead of `rm -rf`, we rename the file with a `.bak` extension. **Why this works:** `systemd` parses drop-in directories (`*.d`) strictly looking for files ending in `.conf`. Any other extension is ignored during the load process, effectively disabling the config while preserving the file for rollback. 

1. Rename the Configuration Move the active configuration file to a backup state. 
   
```bash
mv ~/.config/systemd/user/swaync.service.d/gpu-fix.conf ~/.config/systemd/user/swaync.service.d/gpu-fix.conf.bak
```


```bash
# Reload the systemd user manager configuration
systemctl --user daemon-reload

# Restart swaync to apply the new execution environment
systemctl --user restart swaync

#verify the status
systemctl --user status swaync
```

2. to roll back the change 

```bash
mv ~/.config/systemd/user/swaync.service.d/gpu-fix.conf.bak ~/.config/systemd/user/swaync.service.d/gpu-fix.conf
```

then reload systemd user dameon like above. 

---
*Script*
## Only for Asus tuf f15 

- [ ] **Asus misconfiguration for asusd D-bus:** :- follow the note for it if you have an asus laptop. [[Asusd Dbus Misconfiguration]]

---

- [ ] make sure to uncomment this line if it's commented to allow for the nvidia gpu to sleep or else xwayland will keep it awake and prevent d3 state. 

```bash
nvim ~/.config/uwsm/env-hyprland
```

> [!tip]+ UN-Comment this line
> ```ini
>export AQ_DRM_DEVICES=/dev/dri/card1
>```

> [!error]+ No longer used because hyprland no longer uses wl-roots. it uses aquamarien
> this should be commented in 
> ```bash
> nvim ~/.config/uwsm/env
> ```
>```
> export WLR_DRM_DEVICES="/dev/dri/card1"
>```

---

### 3. (Optional) Package Management & Software Installation

- [ ] **Install Tools:**
    - [ ] Install `ollama`. [[+ MOC Ollama]]
    - [ ] Install `faster-whisper`. [[Faster Whisper]] (Recommanded, for CPU only) TTS
    - [ ] Install Nvidia `parakeet` [[Parakeet]] (Recommanded, requires nvidia) TTS
	- [ ] Install `kokoro` [[Kokoro Rust CPU]] (Recommanded, for CPU only) STT
	- [ ] Install `kokoro` [[Kokoro GPU]] (Recommanded, requires nvidia) STT
	- [ ] Install `DaVinci Resolve` [[DaVinci Resolve]]
	- [ ] Install `Steam, wine, lutris` [[Gaming]]
	- [ ] Install `Waydroid` :- Android container. lightweight. [[+ MOC Waydroid]]

---


---

- [ ] **Configure Thunar:** Set up the right-click "Open Terminal Here" custom action.

>Thunar > Edit > Configure Custom Actions... > Open Terminal Here > Edit the Currenctly selected action > delete everything in the `Command:` box and type your terminal's name eg kitty. 

---

### 5. Services & Networking

Enable essential background services and disable ones you don't need.

- [ ] **Optimize Network Services:** Disable any extra NetworkManager services that are not required. [[Network Manager]]

```bash
sudo systemctl disable NetworkManager-wait-online.service
```

---
*Script*
- [ ] **Set up FTP:** Configure your FTP client or server as needed. [[+ MOC FTP]]

---
*Script*
### Application Configuration

- [ ] terminal tldr update for commands example. 

```bash
tldr --update
```

---
**OBSELETED** (SKIP this)
- [ ] **`firefox`:** Apply your custom `userChrome.css` for the side-panel modifications., hardware acceleration and smoothscrooling and other stuff refer to [[+ MOC Firefox]]

---

- [ ] **`uget + aria2`:**  uget is the front end gui which uses curl for downlaoding stuff by default, change it to uget engine. which is a superior engine for downloading. 
**Edit>Settings> Plug-in> Plug-in matching order:aria2**

---
*Script*
- [ ] **`spotify`:** without adds script [[Spotify]]

---
*Script*
- [ ] `Spicetify matugen colros.` [[Spicetify instructions]]

---

- [ ] **Obsidian** download `hider`, `copilot` plugins and also downlaod the `primary` theme. 

---
*Script*
### 7. Create a new bare repo or Re-Link exisiting github repo to continue backing up to it. 

- [ ] create a new github repo to start backing up. [[git_bare_repo_setup]]

**OR**

- [ ] Follow these steps after you've already checked out and restored all the files from the github repo  [[Relink to my existing github repo for backup after Fresh Install]]


---
*Script*
Free up storage by clearing package cache for paru and pacman 
  
- [ ] **Free up storage by clearing pacman Cache**

```bash
sudo pacman -Scc
```

- [ ] **Free up storage by clearing paru Cache**
```bash
paru -Scc
```

---
