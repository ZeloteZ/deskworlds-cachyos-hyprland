#!/usr/bin/env bash
# Install the CachyOS/Hyprland host, its local scene copy, and a user service.
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project=$(dirname -- "$here")
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
bin_home=${XDG_BIN_HOME:-"$HOME/.local/bin"}
install_root="$data_home/deskworlds"
unit_dir="$config_home/systemd/user"
unit="$unit_dir/deskworlds.service"
install_packages=true
start_service=true

usage() {
  cat <<'EOF'
Usage: bash linux/install.sh [options]

Options:
  --no-packages  Do not install missing CachyOS/Arch packages.
  --no-start     Install files and the service, but do not enable/start it.
  -h, --help     Show this help.
EOF
}

while (($#)); do
  case "$1" in
    --no-packages) install_packages=false ;;
    --no-start) start_service=false ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

for required in \
  "$project/scenes/riverscape/wallpaper.html" \
  "$project/scenes/reefscape/wallpaper.html" \
  "$project/scenes/bettascape/wallpaper.html" \
  "$project/vendor/three.module.js" \
  "$here/deskworlds.py" \
  "$here/deskworldsctl.py"; do
  if [[ ! -f "$required" ]]; then
    echo "Incomplete Deskworlds checkout: missing $required" >&2
    exit 1
  fi
done

packages=(python python-gobject gtk3 gtk-layer-shell webkit2gtk-4.1)
missing=()
if command -v pacman >/dev/null 2>&1; then
  for package in "${packages[@]}"; do
    pacman -Q "$package" >/dev/null 2>&1 || missing+=("$package")
  done
elif $install_packages; then
  echo "This installer targets CachyOS/Arch Linux and requires pacman." >&2
  exit 1
fi

if ((${#missing[@]})); then
  if $install_packages; then
    echo "Installing required packages: ${missing[*]}"
    sudo pacman -S --needed -- "${missing[@]}"
  else
    echo "Missing required packages: ${missing[*]}" >&2
    echo "Install them, then rerun this installer." >&2
    exit 1
  fi
fi

staging=$(mktemp -d "${TMPDIR:-/tmp}/deskworlds-install.XXXXXX")
cleanup() { rm -rf -- "$staging"; }
trap cleanup EXIT
mkdir -p "$staging/deskworlds"
cp -a -- "$project/scenes" "$project/vendor" "$project/ui" "$here" \
  "$staging/deskworlds/"
find "$staging/deskworlds/scenes" -type d -name tests -prune -exec rm -rf -- {} +

mkdir -p -- "$data_home"
rm -rf -- "$install_root.previous"
if [[ -e "$install_root" ]]; then
  mv -- "$install_root" "$install_root.previous"
fi
mv -- "$staging/deskworlds" "$install_root"
rm -rf -- "$install_root.previous"

mkdir -p -- "$bin_home"
cat >"$bin_home/deskworlds" <<EOF
#!/bin/sh
exec /usr/bin/python3 "$install_root/linux/deskworlds.py" --root "$install_root" "\$@"
EOF
cat >"$bin_home/deskworldsctl" <<EOF
#!/bin/sh
exec /usr/bin/python3 "$install_root/linux/deskworldsctl.py" "\$@"
EOF
chmod 0755 "$bin_home/deskworlds" "$bin_home/deskworldsctl"

mkdir -p -- "$unit_dir"
cat >"$unit" <<EOF
[Unit]
Description=Deskworlds live wallpaper for Hyprland
Documentation=https://github.com/ZeloteZ/deskworlds-cachyos-hyprland
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=$bin_home/deskworlds
Restart=on-failure
RestartSec=3
Environment=GDK_BACKEND=wayland
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
if $start_service; then
  # Import what is available now. The Python host can also recover these values from
  # Hyprland and Wayland sockets after a future login.
  systemctl --user import-environment \
    WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_CURRENT_DESKTOP \
    XDG_SESSION_TYPE DISPLAY 2>/dev/null || true
  systemctl --user enable --now deskworlds.service
fi

cat <<EOF
Deskworlds is installed for CachyOS + Hyprland.

  Data:       $install_root
  Controller: $bin_home/deskworldsctl
  Service:    deskworlds.service

Try:
  deskworldsctl status
  deskworldsctl world coral-reef
  deskworldsctl pause

Logs:
  journalctl --user -u deskworlds.service -f
EOF
