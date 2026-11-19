
declare -ra ISO_SEQUENCE=(
  "010_environment_prep.sh --auto"
  "020_set_variables.py"
  "030_partitioning.sh --auto"
  "040_disk_mount.sh --auto"
  "050_mirrorlist.sh | IGNORE"
  "060_console_fix.sh"
  "070_pacstrap.sh --auto"
  "090_fstab.sh --auto"
)

declare -ra CHROOT_SEQUENCE=(
  "100_etc_skel.sh --auto"
  "101_skel_files_precision_edit.sh --inject"
  "110_post_chroot.sh --auto"
  "115_tty_autologin.sh --auto"
  "120_mkintcpip_optimizer.sh | IGNORE"
  "125_mkinitcpio_hooks_disable.sh"
  "130_chroot_package_installer.sh --auto"
  "135_plymouth_setup.sh"
# "150_limine_bootloader.sh --auto"
  "154_mkinitcpio_hooks_restore.sh"
  "155_limine_setup.sh --auto"
  "156_snapper_isolation_subvolume.sh --auto"
  "157_snapper_pacman_hooks.sh --auto"
  "158_mkinitcpio_restore_and_generate.sh"
  "160_zram_config.sh"
  "170_services.sh"
  "180_exiting_unmounting.sh --auto"
)






arch_iso_scripts/online ❯ tree
.
├── 000_dusky_arch_install.sh
├── 020_environment_prep.sh
├── 030_partitioning.sh
├── 040_disk_mount.sh
├── 050_mirrorlist.sh
├── 060_console_fix.sh
├── 070_pacstrap.sh
├── 080_script_directories_population_in_chroot.sh
├── 090_fstab.sh
├── 100_etc_skel.sh
├── 101_skel_files_precision_edit.sh
├── 110_post_chroot.sh
├── 115_tty_autologin.sh
├── 120_mkintcpip_optimizer.sh
├── 125_mkinitcpio_hooks_disable.sh
├── 130_chroot_package_installer.sh
├── 135_plymouth_setup.sh
├── 150_limine_bootloader.sh
├── 151_systemd_bootloader.sh
├── 152_grub.sh
├── 154_mkinitcpio_hooks_restore.sh
├── 155_limine_setup.sh
├── 156_snapper_isolation_subvolume.sh
├── 157_snapper_pacman_hooks_for_limine.sh
├── 158_mkinitcpio_restore_and_generate.sh
├── 160_zram_config.sh
├── 170_services.sh
├── 180_exiting_unmounting.sh
├── arch_chroot_switch.sh
└── deploy_dotfiles.sh

1 directory, 30 files
