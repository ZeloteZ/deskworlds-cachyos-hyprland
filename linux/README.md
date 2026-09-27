# Deskworlds for CachyOS + Hyprland

This directory contains the experimental native Linux host for Deskworlds. It keeps the existing Three.js scenes unchanged and presents them as real Wayland background surfaces instead of recording them to video.

## What the port does

- Creates one `wlr-layer-shell` **background** surface for every monitor.
- Embeds the local wallpaper page in WebKitGTK with WebGL enabled.
- Lets desktop and application input pass through the wallpaper.
- Reads the global cursor position through Hyprland's IPC socket and forwards synthetic pointer movement to each scene, so fish still react to the cursor without stealing clicks.
- Detects ordinary Hyprland windows once per second and lowers or stops the frame rate when the desktop is mostly or completely covered.
- Stops rendering while an output has DPMS disabled and applies a separate frame-rate ceiling on battery power.
- Stores the selected world, pause state, and render limits in `$XDG_CONFIG_HOME/deskworlds/config.json`.
- Exposes a private `$XDG_RUNTIME_DIR/deskworlds/control.sock` for `deskworldsctl`.

No Node.js process, browser tab, internet connection, or XWayland window is needed at runtime.

## Install

From the repository root:

```sh
bash linux/install.sh
```

The installer uses the official CachyOS/Arch packages:

```text
python
python-gobject
gtk3
gtk-layer-shell
webkit2gtk-4.1
```

It copies the runtime to `$XDG_DATA_HOME/deskworlds`, installs `deskworlds` and `deskworldsctl` in `$HOME/.local/bin`, and enables the user service `deskworlds.service`.

Skip package installation or the initial service start when needed:

```sh
bash linux/install.sh --no-packages
bash linux/install.sh --no-start
```

## Control it

```sh
deskworldsctl status
deskworldsctl world riverbed
deskworldsctl world coral-reef
deskworldsctl world betta
deskworldsctl feed
deskworldsctl pause
deskworldsctl resume
deskworldsctl reload
```

The wallpaper intentionally never accepts clicks or keyboard focus. Feeding and world changes therefore go through `deskworldsctl`; a Waybar module or tray UI can be added on top of the same control socket later.

## Development run

Install the dependencies, stop an installed copy, then run the checkout directly:

```sh
systemctl --user stop deskworlds.service
python3 linux/deskworlds.py --root . --debug
```

In another terminal:

```sh
python3 linux/deskworldsctl.py status
```

Run the platform-neutral test suite and syntax checks with:

```sh
python3 -m unittest discover -s linux/tests -v
python3 -m py_compile linux/*.py
bash -n linux/install.sh linux/uninstall.sh
```

## Configuration

The initial configuration is:

```json
{
  "battery_fps": 30,
  "covered_fps": 20,
  "max_fps": 30,
  "paused": false,
  "pointer_fps": 30,
  "world": "riverscape"
}
```

`world` uses the source directory names `riverscape`, `reefscape`, and `bettascape`. `deskworldsctl` also accepts their display names.

Changes made through the controller are written atomically. Manual changes take effect after:

```sh
systemctl --user restart deskworlds.service
```

## Logs and troubleshooting

Follow the service log:

```sh
journalctl --user -u deskworlds.service -f
```

Check whether WebKit has loaded each output and whether Hyprland IPC was found:

```sh
deskworldsctl status
```

When the service was started before Hyprland exported its environment, the host discovers the newest Hyprland and Wayland sockets under `$XDG_RUNTIME_DIR`. The systemd service restarts until the compositor is ready.

A blank or frozen output should leave a WebKit load error, JavaScript error, or web-process termination reason in the journal. The host automatically reloads a terminated WebKit process once after two seconds.

## Uninstall

```sh
bash linux/uninstall.sh
```

Saved world and pause settings remain. Remove them too with:

```sh
bash linux/uninstall.sh --purge
```

## Current scope

This first port is deliberately Hyprland-specific because reliable global pointer coordinates are not exposed to ordinary Wayland clients. The rendering and layer-shell portions are compositor-neutral; supporting another wlroots compositor primarily requires a replacement cursor/window-state adapter.
