#!/usr/bin/env python3
"""Deskworlds live wallpaper host for CachyOS and Hyprland.

The existing Three.js scenes are embedded in WebKitGTK. Each output gets a
click-through wlr-layer-shell background surface. Hyprland's socket1 IPC supplies
cursor positions and ordinary window geometry without compromising desktop input.
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import traceback
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

try:
    from .deskworlds_core import (
        ConfigStore,
        HyprlandIPC,
        Rect,
        WORLD_PAGES,
        WORLD_TITLES,
        bootstrap_hyprland_environment,
        calculate_exposure,
        choose_frame_rate,
        match_hyprland_monitor,
        running_on_battery,
        visible_client_rectangles,
        xdg_runtime_dir,
    )
except ImportError:  # Executed directly from linux/deskworlds.py.
    from deskworlds_core import (  # type: ignore
        ConfigStore,
        HyprlandIPC,
        Rect,
        WORLD_PAGES,
        WORLD_TITLES,
        bootstrap_hyprland_environment,
        calculate_exposure,
        choose_frame_rate,
        match_hyprland_monitor,
        running_on_battery,
        visible_client_rectangles,
        xdg_runtime_dir,
    )


bootstrap_hyprland_environment()

import gi  # noqa: E402  (the Wayland environment must be prepared first)

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell, WebKit2  # noqa: E402


LOGGER = logging.getLogger("deskworlds")
SCHEME = "deskworlds"
HOST = "local"
MAX_CONTROL_MESSAGE = 16 * 1024

BACKGROUND_COLORS: dict[str, tuple[float, float, float, float]] = {
    "riverscape": (0.031, 0.055, 0.047, 1.0),
    "reefscape": (0.043, 0.094, 0.145, 1.0),
    "bettascape": (0.0, 0.0, 0.0, 1.0),
}

POINTER_BRIDGE = r"""
(() => {
  window.scenePointerCount = 0;
  window.scenePointer = (x, y) => {
    const canvas = document.querySelector('#scene');
    window.scenePointerCount++;
    if (canvas) {
      canvas.dispatchEvent(new PointerEvent('pointermove', {
        clientX: x,
        clientY: y,
        bubbles: true,
      }));
    }
  };
  window.scenePointerOut = () => {
    const canvas = document.querySelector('#scene');
    if (canvas) canvas.dispatchEvent(new PointerEvent('pointerleave'));
  };
})();
"""

REPORT_BRIDGE = r"""
(() => {
  const report = (text) => {
    try {
      window.webkit?.messageHandlers?.report?.postMessage(String(text));
    } catch (_) {}
  };
  for (const level of ['error', 'warn']) {
    const original = console[level];
    console[level] = (...parts) => {
      report(parts.map((part) => part && part.stack ? part.stack : part).join(' '));
      original.apply(console, parts);
    };
  }
  addEventListener('error', (event) =>
    report(`${event.message} at ${event.filename}:${event.lineno}`));
  addEventListener('unhandledrejection', (event) => report(event.reason));
})();
"""


class LocalScheme:
    """Serve only files below the installed project root to WebKitGTK."""

    EXTRA_TYPES = {
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".json": "application/json",
        ".bin": "application/octet-stream",
        ".glsl": "text/plain",
    }

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def register(self, context: WebKit2.WebContext) -> None:
        context.register_uri_scheme(SCHEME, self._handle)
        security = context.get_security_manager()
        for method_name in (
            "register_uri_scheme_as_local",
            "register_uri_scheme_as_secure",
            "register_uri_scheme_as_cors_enabled",
        ):
            method = getattr(security, method_name, None)
            if method is not None:
                method(SCHEME)

    def _handle(self, request: WebKit2.URISchemeRequest) -> None:
        try:
            path = unquote(urlsplit(request.get_uri()).path).lstrip("/")
            if not path:
                raise FileNotFoundError("empty Deskworlds URI")
            candidate = (self.root / path).resolve()
            if os.path.commonpath((self.root, candidate)) != str(self.root):
                raise PermissionError("path escaped the Deskworlds root")
            data = candidate.read_bytes()
            mime_type = self.EXTRA_TYPES.get(candidate.suffix.lower())
            if mime_type is None:
                mime_type = mimetypes.guess_type(candidate.name)[0]
            stream = Gio.MemoryInputStream.new_from_data(data, None)
            request.finish(stream, len(data), mime_type or "application/octet-stream")
        except Exception as error:  # WebKit needs a completed request even for failures.
            LOGGER.error("URI %s failed: %s", request.get_uri(), error)
            message = f"Deskworlds resource unavailable: {error}\n".encode("utf-8")
            stream = Gio.MemoryInputStream.new_from_data(message, None)
            request.finish(stream, len(message), "text/plain")


class WallpaperWindow:
    """One live WebKit scene on one GDK monitor."""

    def __init__(self, app: "DesktopHost", monitor: Gdk.Monitor, index: int) -> None:
        self.app = app
        self.monitor = monitor
        self.index = index
        self.loaded = False
        self.inside = False
        self.rate = -1
        self.on_battery = False
        self.exposure = 1.0
        self.hyprland_name: str | None = None

        geometry = monitor.get_geometry()
        self.frame = Rect(
            float(geometry.x),
            float(geometry.y),
            float(geometry.width),
            float(geometry.height),
        )

        self.manager = WebKit2.UserContentManager()
        for name, callback in (
            ("ready", self._on_ready),
            ("report", self._on_report),
        ):
            if not self.manager.register_script_message_handler(name):
                raise RuntimeError(f"could not register WebKit message handler {name}")
            self.manager.connect(f"script-message-received::{name}", callback)

        for source in (POINTER_BRIDGE, REPORT_BRIDGE):
            self.manager.add_script(
                WebKit2.UserScript.new(
                    source,
                    WebKit2.UserContentInjectedFrames.TOP_FRAME,
                    WebKit2.UserScriptInjectionTime.START,
                    None,
                    None,
                )
            )

        self.view = WebKit2.WebView.new_with_user_content_manager(self.manager)
        self.view.set_hexpand(True)
        self.view.set_vexpand(True)
        self.view.connect("context-menu", lambda *_args: True)
        self.view.connect("load-failed", self._on_load_failed)
        self.view.connect("web-process-terminated", self._on_web_process_terminated)

        settings = self.view.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_webgl(True)
        try:
            settings.set_hardware_acceleration_policy(
                WebKit2.HardwareAccelerationPolicy.ALWAYS
            )
        except (AttributeError, TypeError):
            pass

        red, green, blue, alpha = BACKGROUND_COLORS[self.app.config["world"]]
        color = Gdk.RGBA()
        color.red = red
        color.green = green
        color.blue = blue
        color.alpha = alpha
        try:
            self.view.set_background_color(color)
        except AttributeError:
            pass

        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title(f"Deskworlds · output {index + 1}")
        self.window.set_decorated(False)
        self.window.set_resizable(True)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.connect("realize", self._make_click_through)
        self.window.connect("delete-event", lambda *_args: True)
        self.window.add(self.view)

        GtkLayerShell.init_for_window(self.window)
        GtkLayerShell.set_namespace(self.window, "deskworlds")
        GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.BACKGROUND)
        GtkLayerShell.set_monitor(self.window, monitor)
        GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.NONE)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.RIGHT,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
        ):
            GtkLayerShell.set_anchor(self.window, edge, True)
        # A negative zone asks the compositor to cover the complete output, including
        # the area behind bars. It does not reserve desktop space for this window.
        GtkLayerShell.set_exclusive_zone(self.window, -1)

        self.window.show_all()
        self.view.load_uri(
            f"{SCHEME}://{HOST}{WORLD_PAGES[self.app.config['world']]}"
        )

    @property
    def display_name(self) -> str:
        parts = [self.monitor.get_manufacturer(), self.monitor.get_model()]
        label = " ".join(part for part in parts if part)
        return label or f"output {self.index + 1}"

    def _make_click_through(self, window: Gtk.Window) -> None:
        gdk_window = window.get_window()
        if gdk_window is not None:
            gdk_window.set_pass_through(True)

    def _on_ready(self, *_args: Any) -> None:
        self.loaded = True
        self._send_state()
        LOGGER.info("%s is ready", self.display_name)

    def _on_report(
        self, _manager: WebKit2.UserContentManager, result: Any
    ) -> None:
        try:
            value = result.get_js_value().to_string()
        except Exception:
            value = str(result)
        LOGGER.warning("%s page: %s", self.display_name, value)

    def _on_load_failed(
        self,
        _view: WebKit2.WebView,
        event: Any,
        uri: str,
        error: GLib.Error,
    ) -> bool:
        LOGGER.error("%s failed to load %s (%s): %s", self.display_name, uri, event, error)
        return False

    def _on_web_process_terminated(
        self, _view: WebKit2.WebView, reason: Any
    ) -> None:
        LOGGER.error("%s WebKit process terminated: %s", self.display_name, reason)
        GLib.timeout_add_seconds(2, self._reload_after_crash)

    def _reload_after_crash(self) -> bool:
        self.loaded = False
        self.view.reload()
        return GLib.SOURCE_REMOVE

    def evaluate(self, script: str) -> None:
        if not self.loaded:
            return
        try:
            self.view.evaluate_javascript(
                script,
                -1,
                None,
                None,
                None,
                None,
                None,
            )
        except (AttributeError, TypeError):
            # Kept for older WebKitGTK 4.1 builds. CachyOS currently exposes the
            # evaluate_javascript API, but the fallback costs almost nothing.
            self.view.run_javascript(script, None, None, None)

    def _send_state(self) -> None:
        if not self.loaded:
            return
        self.evaluate(
            "typeof scenePower === 'function' && "
            f"scenePower({'true' if self.on_battery else 'false'});"
            "typeof sceneRate === 'function' && "
            f"sceneRate({self.rate if self.rate >= 0 else 0});"
        )

    def set_policy(self, rate: int, on_battery: bool, exposure: float) -> None:
        changed = rate != self.rate or on_battery != self.on_battery
        self.exposure = exposure
        self.rate = rate
        self.on_battery = on_battery
        if rate == 0 and self.inside:
            self.pointer_out()
        if changed:
            self._send_state()

    def set_pointer(self, x: float, y: float) -> None:
        if self.rate <= 0 or not self.loaded:
            return
        self.inside = True
        self.evaluate(f"scenePointer({x:.2f}, {y:.2f})")

    def pointer_out(self) -> None:
        if self.inside and self.loaded:
            self.evaluate("scenePointerOut()")
        self.inside = False

    def feed(self) -> None:
        if self.rate > 0:
            self.evaluate("typeof sceneFeed === 'function' && sceneFeed()")

    def destroy(self) -> None:
        self.loaded = False
        try:
            self.manager.unregister_script_message_handler("ready")
            self.manager.unregister_script_message_handler("report")
        except Exception:
            pass
        self.window.destroy()


class ControlServer:
    """Private newline-delimited JSON control socket for deskworldsctl."""

    def __init__(self, path: Path, dispatch: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.path = path
        self.dispatch = dispatch
        self.stop_event = threading.Event()
        self.listener: socket.socket | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(0.15)
            try:
                probe.connect(str(self.path))
            except OSError:
                self.path.unlink(missing_ok=True)
            else:
                raise RuntimeError("another Deskworlds instance is already running")
            finally:
                probe.close()

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        listener.listen(8)
        listener.settimeout(0.5)
        self.listener = listener
        self.thread = threading.Thread(
            target=self._serve,
            name="deskworlds-control",
            daemon=True,
        )
        self.thread.start()

    def _serve(self) -> None:
        assert self.listener is not None
        while not self.stop_event.is_set():
            try:
                connection, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(2.0)
                try:
                    buffer = bytearray()
                    while len(buffer) <= MAX_CONTROL_MESSAGE:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        if b"\n" in chunk:
                            break
                    if len(buffer) > MAX_CONTROL_MESSAGE:
                        raise ValueError("control message is too large")
                    request = json.loads(bytes(buffer).split(b"\n", 1)[0])
                    if not isinstance(request, dict):
                        raise ValueError("control message must be a JSON object")
                    response = self._dispatch_on_main(request)
                except Exception as error:
                    response = {"ok": False, "error": str(error)}
                try:
                    connection.sendall((json.dumps(response) + "\n").encode("utf-8"))
                except OSError:
                    pass

    def _dispatch_on_main(self, request: dict[str, Any]) -> dict[str, Any]:
        done = threading.Event()
        result: dict[str, Any] = {}

        def run() -> bool:
            try:
                result.update(self.dispatch(request))
            except Exception as error:
                LOGGER.exception("control command failed")
                result.update({"ok": False, "error": str(error)})
            finally:
                done.set()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(run)
        if not done.wait(timeout=5.0):
            return {"ok": False, "error": "desktop host did not answer in time"}
        return result

    def stop(self) -> None:
        self.stop_event.set()
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.path.unlink(missing_ok=True)


class DesktopHost:
    """Coordinates outputs, render policy, cursor forwarding, and commands."""

    def __init__(self, root: Path, config_path: Path | None = None) -> None:
        self.root = root.resolve()
        self._validate_root()
        self.store = ConfigStore(config_path)
        self.config = self.store.load()
        self.ipc = HyprlandIPC()
        self.context = WebKit2.WebContext.get_default()
        LocalScheme(self.root).register(self.context)
        self.display = Gdk.Display.get_default()
        if self.display is None:
            raise RuntimeError("no Wayland display is available")

        self.windows: list[WallpaperWindow] = []
        self.last_cursor: tuple[float, float] | None = None
        self.on_battery = running_on_battery()
        self.latest_monitors: list[dict[str, Any]] = []
        self.latest_clients: list[dict[str, Any]] = []
        self.stopping = False

        control_path = xdg_runtime_dir() / "deskworlds" / "control.sock"
        self.control = ControlServer(control_path, self.dispatch)
        self.control.start()

        self.display.connect("monitor-added", self._monitors_changed)
        self.display.connect("monitor-removed", self._monitors_changed)
        self.rebuild_windows()

        GLib.timeout_add(1000, self.refresh_policy)
        pointer_interval = max(8, round(1000 / self.config["pointer_fps"]))
        GLib.timeout_add(pointer_interval, self.track_pointer)
        GLib.unix_signal_add(
            GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._signal_quit
        )
        GLib.unix_signal_add(
            GLib.PRIORITY_DEFAULT, signal.SIGINT, self._signal_quit
        )

    def _validate_root(self) -> None:
        missing = [
            relative
            for relative in (
                "scenes/riverscape/wallpaper.html",
                "scenes/reefscape/wallpaper.html",
                "scenes/bettascape/wallpaper.html",
                "vendor/three.module.js",
            )
            if not (self.root / relative).is_file()
        ]
        if missing:
            raise RuntimeError(
                f"{self.root} is not a complete Deskworlds install; missing: "
                + ", ".join(missing)
            )

    def _monitors_changed(self, *_args: Any) -> None:
        GLib.idle_add(self.rebuild_windows)

    def rebuild_windows(self) -> bool:
        for window in self.windows:
            window.destroy()
        self.windows.clear()
        for index in range(self.display.get_n_monitors()):
            monitor = self.display.get_monitor(index)
            self.windows.append(WallpaperWindow(self, monitor, index))
        self.last_cursor = None
        self.refresh_policy()
        LOGGER.info(
            "showing %s on %d output(s)",
            WORLD_TITLES[self.config["world"]],
            len(self.windows),
        )
        return GLib.SOURCE_REMOVE

    def refresh_policy(self) -> bool:
        if self.stopping:
            return GLib.SOURCE_REMOVE

        monitors = self.ipc.request("monitors", json_output=True)
        clients = self.ipc.request("clients", json_output=True)
        if isinstance(monitors, list):
            self.latest_monitors = [item for item in monitors if isinstance(item, dict)]
        if isinstance(clients, list):
            self.latest_clients = [item for item in clients if isinstance(item, dict)]
        self.on_battery = running_on_battery()

        for window in self.windows:
            monitor = match_hyprland_monitor(window.frame, self.latest_monitors)
            if monitor is None:
                window.hyprland_name = None
                exposure = 1.0
                dpms_on = True
            else:
                window.hyprland_name = str(monitor.get("name") or "") or None
                blockers = visible_client_rectangles(monitor, self.latest_clients)
                exposure = calculate_exposure(window.frame, blockers)
                dpms_on = bool(monitor.get("dpmsStatus", True))

            rate = choose_frame_rate(
                exposure,
                paused=self.config["paused"],
                dpms_on=dpms_on,
                on_battery=self.on_battery,
                max_fps=self.config["max_fps"],
                covered_fps=self.config["covered_fps"],
                battery_fps=self.config["battery_fps"],
            )
            window.set_policy(rate, self.on_battery, exposure)
        return GLib.SOURCE_CONTINUE

    def track_pointer(self) -> bool:
        if self.stopping:
            return GLib.SOURCE_REMOVE
        if not any(window.rate > 0 for window in self.windows):
            return GLib.SOURCE_CONTINUE

        position = self.ipc.cursor_position()
        if position is None:
            return GLib.SOURCE_CONTINUE
        if self.last_cursor is not None:
            if (
                abs(position[0] - self.last_cursor[0]) <= 0.20
                and abs(position[1] - self.last_cursor[1]) <= 0.20
            ):
                return GLib.SOURCE_CONTINUE
        self.last_cursor = position

        x, y = position
        for window in self.windows:
            if window.frame.contains(x, y):
                window.set_pointer(x - window.frame.x, y - window.frame.y)
            else:
                window.pointer_out()
        return GLib.SOURCE_CONTINUE

    def _status(self) -> dict[str, Any]:
        return {
            "world": self.config["world"],
            "world_title": WORLD_TITLES[self.config["world"]],
            "paused": self.config["paused"],
            "on_battery": self.on_battery,
            "hyprland_socket": str(self.ipc.socket_path) if self.ipc.socket_path else None,
            "outputs": [
                {
                    "index": window.index,
                    "name": window.display_name,
                    "hyprland_name": window.hyprland_name,
                    "frame": window.frame.as_dict(),
                    "fps": window.rate,
                    "exposure": round(window.exposure, 3),
                    "loaded": window.loaded,
                }
                for window in self.windows
            ],
        }

    def _save(self) -> None:
        self.store.save(self.config)

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        command = str(request.get("command", "")).strip().lower()
        if command == "status":
            return {"ok": True, "status": self._status()}
        if command in {"pause", "resume", "toggle"}:
            if command == "toggle":
                self.config["paused"] = not self.config["paused"]
            else:
                self.config["paused"] = command == "pause"
            self._save()
            self.refresh_policy()
            return {"ok": True, "status": self._status()}
        if command == "feed":
            for window in self.windows:
                window.feed()
            return {"ok": True, "message": "Fed every running world."}
        if command == "world":
            world = str(request.get("value", ""))
            if world not in WORLD_PAGES:
                raise ValueError(
                    "unknown world; expected one of " + ", ".join(WORLD_PAGES)
                )
            if world != self.config["world"]:
                self.config["world"] = world
                self._save()
                self.rebuild_windows()
            return {"ok": True, "status": self._status()}
        if command == "reload":
            self.rebuild_windows()
            return {"ok": True, "status": self._status()}
        if command == "quit":
            GLib.idle_add(self.quit)
            return {"ok": True, "message": "Deskworlds is stopping."}
        raise ValueError("unknown command")

    def _signal_quit(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE

    def quit(self) -> bool:
        if self.stopping:
            return GLib.SOURCE_REMOVE
        self.stopping = True
        Gtk.main_quit()
        return GLib.SOURCE_REMOVE

    def run(self) -> None:
        try:
            Gtk.main()
        finally:
            self.stopping = True
            self.control.stop()
            for window in self.windows:
                window.destroy()
            self.windows.clear()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Deskworlds as a live Hyprland wallpaper."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="project/install root containing scenes, vendor, and ui",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="override the XDG config file (mainly useful for testing)",
    )
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    return parser.parse_args(argv)


def initialise_gtk() -> bool:
    try:
        result = Gtk.init_check([])
    except TypeError:
        result = Gtk.init_check()
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not initialise_gtk():
        LOGGER.error(
            "Wayland is not ready. The systemd service will retry after Hyprland starts."
        )
        return 75
    try:
        host = DesktopHost(args.root, args.config)
        host.run()
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        LOGGER.error("startup failed: %s", error)
        if args.debug:
            traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
