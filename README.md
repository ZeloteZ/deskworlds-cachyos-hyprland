# Deskworlds

[![Watch Riverbed](docs/images/riverscape.gif)](docs/videos/riverscape.mp4)

[![Watch Betta](docs/images/bettascape.gif)](docs/videos/bettascape.mp4)

Have you always wanted a little living world on your desktop? Now you can have one :)

Each world is a live 3D scene that reacts to your cursor. There are three so far, all underwater: **Riverbed**, a planted river where a school of fish competes for food, **Coral reef**, a coral reef with clownfish and cleaner shrimp, and **Betta**, a single halfmoon betta on a black background.

Every scene is rendered live with Three.js and WebGL2. Everything runs locally, with no account or internet connection needed after setup. Desktop wallpaper hosts are available for **macOS** and, experimentally, **CachyOS/Arch Linux with Hyprland**. All three worlds also run in a browser.

## Install on CachyOS + Hyprland

The Linux host uses a real Wayland layer-shell background and WebKitGTK. It stays below windows, never takes keyboard or mouse input, and forwards Hyprland's global cursor position to the scene so the creatures can still react.

Clone or download this repository, open a terminal in the project folder, and run:

```sh
bash linux/install.sh
```

The installer checks and, when necessary, offers to install these official CachyOS/Arch packages: `python`, `python-gobject`, `gtk3`, `gtk-layer-shell`, and `webkit2gtk-4.1`. It then installs and starts the user service `deskworlds.service`. Node.js is not needed at runtime.

Control the running wallpaper from any terminal:

```sh
deskworldsctl status
deskworldsctl world riverbed
deskworldsctl world coral-reef
deskworldsctl world betta
deskworldsctl feed
deskworldsctl pause
deskworldsctl resume
```

Every monitor gets its own renderer. The host lowers the frame rate when normal windows cover most of an output and stops it when the desktop is almost entirely covered or DPMS has switched the output off. World and pause choices survive restarts.

See [the CachyOS/Hyprland documentation](linux/README.md) for architecture, development commands, configuration, logs, and troubleshooting. `npm run wallpaper:linux` invokes the same installer.

## Install on Mac

You need macOS 13 or newer and the Xcode command line tools. To install the tools, open Terminal and run:

```sh
xcode-select --install
```

Wait for that installation to finish. Download and unzip this repository, or clone it, then open Terminal in the project folder and run:

```sh
sh wallpaper/install.sh
```

The script builds the app for your Mac, installs it at `~/Applications/Deskworlds.app`, and starts it. It also adds a login item so your world starts when you sign in. Allow about 20 seconds for the first frame to appear.

The installer doesn't change your desktop picture. The world draws on top of it, and your own wallpaper still shows at login and in Mission Control.

You don't need Node.js for the wallpaper. If you already have it, `npm run wallpaper` or `npm run wallpaper:macos` runs the same installer.

## Use the macOS wallpaper

Click the Deskworlds icon in the menu bar:

- **World** switches every screen between Riverbed, Coral reef and Betta and remembers your choice.
- **Feed** drops ten pellets into each screen's scene, eight in Coral reef or six to eight in Betta. Uneaten pellets dissolve after 20–40 seconds of running simulation time in Riverbed, 36 seconds in Coral reef and 30 seconds in Betta, measured from when they touch the water.
- **Pause / Resume** controls the animation. Your choice is remembered across restarts.
- **Quit** closes the app until you open it again or next sign in.

Move your cursor through a scene to see its creatures react. Desktop icons, clicks and dragging work as usual. To feed them, use the menu; clicking the desktop does not drop food.

## FAQ

### Does it work on Windows or Linux?

The experimental Linux desktop host currently targets CachyOS/Arch Linux running Hyprland on Wayland. The renderer itself is compositor-neutral, but global cursor and window-state integration currently uses Hyprland IPC. Windows does not yet have a desktop host. The browser preview works anywhere with WebGL2.

### Will it drain my battery?

It uses more power than a still wallpaper because it renders a 3D scene. The amount depends on the machine, screen resolution, quality profile and number of displays. There isn't a measured battery-life estimate yet.

All three scenes stop rendering when paused or hidden. The desktop hosts also respond to window coverage, output sleep and power state. With the default Balanced profile, every world uses these limits:

| Desktop state | Frame rate |
| --- | --- |
| Clearly visible | Up to 30 fps on Linux; host-dependent on macOS |
| Mostly covered by windows | Up to 20 fps |
| Almost entirely covered | Stopped |
| Paused, locked where detectable, or sleeping display | Stopped |

Pause it when you want a still wallpaper, or quit/stop the service to close it completely. The browser previews offer Eco, Balanced and Detail profiles; actual frame rates depend on the device and scene. Battery life has not been measured.

### Does it monitor my keystrokes?

No. The wallpaper does not listen to typing in other apps or record keystrokes. All the browser previews handle Space to pause or resume, F for fullscreen, and H to hide or show controls while a scene has focus.

The wallpaper reads your cursor position so the creatures can react. It also checks window positions and sizes to estimate how much of the desktop is visible. It does not capture the contents of those windows, store cursor history, or send this information anywhere.

On Linux, the background surfaces explicitly refuse keyboard focus and pass pointer input through to the desktop and applications. Control commands use a private Unix socket owned by the current user.

### Does it need internet access or special permissions?

Once installed, Deskworlds works offline. Its code, textures and Three.js library are bundled with the app. There are no analytics or external services.

The macOS app does not request Accessibility, Input Monitoring or Screen Recording access. The Linux host reads Hyprland's local IPC socket as the current desktop user and does not need root privileges after package installation.

### Why has the scene stopped moving?

On macOS, click the menu bar icon to see the current status. On Linux, run:

```sh
deskworldsctl status
```

The wallpaper deliberately stops when it is almost entirely covered, paused, or while its output is asleep. On macOS it also stops in Low Power Mode and while the screen is locked.

If Reduce Motion is enabled in macOS, the wallpaper starts paused unless you have already saved a different choice. Choose **Resume** to animate it. Low Power Mode must be turned off before animation can resume.

### Can I use multiple monitors?

Yes. Each display gets its own world, and **Feed** or `deskworldsctl feed` drops food on every display. Each one renders separately, so more displays can increase power use.

### Do I need to leave Terminal open?

No. Both desktop hosts run independently after installation. macOS uses a LaunchAgent; Linux uses a systemd user service.

### How do I update it?

Download or pull the latest source, then rerun the installer for your platform. Editing the source alone does not update the installed copy.

```sh
# CachyOS + Hyprland
bash linux/install.sh

# macOS
sh wallpaper/install.sh
```

Deskworlds was called Desktop Habitats, and before that Aquatica. The macOS installer removes either earlier app and its login item before starting Deskworlds. Your Desktop Habitats scene and pause choices carry over; Aquatica's saved preference is left behind.

### How do I remove it and get my old wallpaper back?

On CachyOS + Hyprland:

```sh
bash linux/uninstall.sh
```

Add `--purge` to remove saved world and pause settings too.

On macOS:

```sh
sh wallpaper/uninstall.sh
```

Or use `npm run unwallpaper`. This stops the app, removes its login item and deletes the installed app. Your wallpaper was never changed, so it is already there underneath. The saved pause and world preferences are retained.

Versions before this one set a still image of the scene as the desktop picture. If you installed one of those, choose your wallpaper in System Settings, then delete `~/Pictures/Desktop Habitats.png`.

## Try it in a browser

With Node.js 20 or newer, run this from the project folder:

```sh
npm start
```

Open [the local preview](http://127.0.0.1:8080). There is no `npm install` step; the library is included. Use `PORT=8081 npm start` if port 8080 is busy, and Ctrl+C to stop the server.

- Click a scene to drop food.
- Move the pointer through a scene to interact.
- In Betta, drag to look around the betta and scroll to zoom. Clicking still drops food.
- Swipe or scroll through the gallery, or use the left and right arrow keys. Open the image or name to enter a scene.
- Use **Pause / Resume**, **Feed**, **Fullscreen** and **Hide controls** in every scene. **Show controls** brings the controls back.
- Press **Space** to pause or resume, **F** for fullscreen, and **H** to hide or show controls while a scene has focus.
- **Quality** offers Eco (20 fps), Balanced (30 fps, the default) and Detail (60 fps). The selection is shared between the scenes and remembered. These are frame-rate caps; lower profiles also reduce rendering resolution.

Reduce Motion starts the preview paused. Serve the page over HTTP; opening `index.html` directly will not load its JavaScript modules. Any static server also works, such as `python3 -m http.server 8080 --bind 127.0.0.1` if you have Python installed.

## Credits and license

Deskworlds is [MIT licensed](LICENSE). Three.js 0.180.0 is bundled under its [MIT license](vendor/THREE-LICENSE.txt).

The rock, wood and sand textures come from Poly Haven under [CC0](https://polyhaven.com/license): [Rock Boulder Dry](https://polyhaven.com/a/rock_boulder_dry), [Rough Wood](https://polyhaven.com/a/rough_wood) and [Sand 01](https://polyhaven.com/a/sand_01). Coral reef's rock mesh, pore maps, coral texture and organism meshes are procedural, generated by the scripts in `tools/`.
