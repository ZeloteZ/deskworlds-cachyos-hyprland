#!/usr/bin/env bash
# Remove the CachyOS/Hyprland host. Saved preferences stay unless --purge is used.
set -euo pipefail

data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
bin_home=${XDG_BIN_HOME:-"$HOME/.local/bin"}
runtime_dir=${XDG_RUNTIME_DIR:-"/run/user/$(id -u)"}
purge=false

case "${1:-}" in
  "") ;;
  --purge) purge=true ;;
  -h|--help)
    echo "Usage: bash linux/uninstall.sh [--purge]"
    exit 0
    ;;
  *) echo "Unknown option: $1" >&2; exit 2 ;;
esac

systemctl --user disable --now deskworlds.service 2>/dev/null || true
rm -f -- "$config_home/systemd/user/deskworlds.service"
systemctl --user daemon-reload
systemctl --user reset-failed deskworlds.service 2>/dev/null || true

rm -rf -- "$data_home/deskworlds"
rm -f -- "$bin_home/deskworlds" "$bin_home/deskworldsctl"
rm -rf -- "$runtime_dir/deskworlds"

if $purge; then
  rm -rf -- "$config_home/deskworlds"
fi

echo "Deskworlds removed."
if ! $purge; then
  echo "Saved settings remain in $config_home/deskworlds (use --purge to remove them)."
fi
