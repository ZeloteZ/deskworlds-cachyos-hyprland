"""Platform-neutral helpers for the Deskworlds Hyprland host.

This module deliberately has no GTK or WebKit imports.  It contains the pieces that
can be tested in an ordinary Python process: Hyprland IPC, geometry, render policy,
configuration, and environment discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import threading
from typing import Any, Iterable, Mapping, Sequence


WORLD_PAGES: dict[str, str] = {
    "riverscape": "/scenes/riverscape/wallpaper.html",
    "reefscape": "/scenes/reefscape/wallpaper.html",
    "bettascape": "/scenes/bettascape/wallpaper.html",
}

WORLD_TITLES: dict[str, str] = {
    "riverscape": "Riverbed",
    "reefscape": "Coral reef",
    "bettascape": "Betta",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "world": "riverscape",
    "paused": False,
    "max_fps": 30,
    "covered_fps": 20,
    "battery_fps": 30,
    "pointer_fps": 30,
}


@dataclass(frozen=True, slots=True)
class Rect:
    """A rectangle in Hyprland's logical, global coordinate system."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def as_dict(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def logical_monitor_rect(monitor: Mapping[str, Any]) -> Rect:
    """Convert one ``hyprctl -j monitors`` item into logical coordinates."""

    scale = max(0.01, _number(monitor.get("scale"), 1.0))
    width = _number(monitor.get("width")) / scale
    height = _number(monitor.get("height")) / scale
    transform = int(_number(monitor.get("transform"), 0))
    if transform in {1, 3, 5, 7}:
        width, height = height, width
    return Rect(
        _number(monitor.get("x")),
        _number(monitor.get("y")),
        max(0.0, width),
        max(0.0, height),
    )


def match_hyprland_monitor(
    frame: Rect, monitors: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """Find the Hyprland output that most closely matches a GDK monitor frame."""

    if not monitors:
        return None

    def score(monitor: Mapping[str, Any]) -> float:
        candidate = logical_monitor_rect(monitor)
        # Position matters slightly more than size.  This also behaves sensibly when
        # a compositor reports a one-pixel rounding difference for scaled outputs.
        return (
            2.0 * abs(candidate.x - frame.x)
            + 2.0 * abs(candidate.y - frame.y)
            + abs(candidate.width - frame.width)
            + abs(candidate.height - frame.height)
        )

    return min(monitors, key=score)


def visible_client_rectangles(
    monitor: Mapping[str, Any], clients: Iterable[Mapping[str, Any]]
) -> list[Rect]:
    """Return visible toplevel windows on the monitor's active workspaces."""

    monitor_id = monitor.get("id")
    workspace_ids: set[int] = set()
    for key in ("activeWorkspace", "specialWorkspace"):
        workspace = monitor.get(key)
        if isinstance(workspace, Mapping):
            try:
                workspace_id = int(workspace.get("id", 0))
            except (TypeError, ValueError):
                continue
            if workspace_id != 0:
                workspace_ids.add(workspace_id)

    rectangles: list[Rect] = []
    for client in clients:
        if client.get("mapped") is False or bool(client.get("hidden", False)):
            continue
        if monitor_id is not None and client.get("monitor") not in (None, monitor_id):
            continue

        workspace = client.get("workspace")
        workspace_id: int | None = None
        if isinstance(workspace, Mapping):
            try:
                workspace_id = int(workspace.get("id"))
            except (TypeError, ValueError):
                workspace_id = None
        if workspace_ids and workspace_id not in workspace_ids:
            continue

        alpha = _number(client.get("alpha"), 1.0)
        if alpha <= 0.05:
            continue

        at = client.get("at")
        size = client.get("size")
        if not (
            isinstance(at, Sequence)
            and not isinstance(at, (str, bytes))
            and len(at) >= 2
            and isinstance(size, Sequence)
            and not isinstance(size, (str, bytes))
            and len(size) >= 2
        ):
            continue

        width = _number(size[0])
        height = _number(size[1])
        if width <= 0 or height <= 0:
            continue
        rectangles.append(Rect(_number(at[0]), _number(at[1]), width, height))

    return rectangles


def calculate_exposure(
    frame: Rect,
    blockers: Sequence[Rect],
    *,
    columns: int = 16,
    rows: int = 10,
) -> float:
    """Estimate what fraction of an output is not covered by normal windows."""

    if frame.width <= 0 or frame.height <= 0:
        return 0.0
    if not blockers:
        return 1.0
    if columns < 1 or rows < 1:
        raise ValueError("sample grid must be positive")

    free = 0
    for column in range(columns):
        for row in range(rows):
            x = frame.x + frame.width * (column + 0.5) / columns
            y = frame.y + frame.height * (row + 0.5) / rows
            if not any(blocker.contains(x, y) for blocker in blockers):
                free += 1
    return free / (columns * rows)


def choose_frame_rate(
    exposure: float,
    *,
    paused: bool,
    dpms_on: bool,
    on_battery: bool,
    max_fps: int = 30,
    covered_fps: int = 20,
    battery_fps: int = 30,
) -> int:
    """Apply the same visible/covered/still policy used by the native hosts."""

    if paused or not dpms_on or exposure < 0.15:
        return 0
    ceiling = max(1, battery_fps if on_battery else max_fps)
    if exposure < 0.40:
        return min(max(1, covered_fps), ceiling)
    return ceiling


def parse_cursor_response(payload: Any) -> tuple[float, float] | None:
    """Accept current and older ``hyprctl cursorpos`` response formats."""

    if isinstance(payload, Mapping):
        if "x" in payload and "y" in payload:
            return _number(payload["x"]), _number(payload["y"])
        position = payload.get("position")
        if isinstance(position, Sequence) and len(position) >= 2:
            return _number(position[0]), _number(position[1])
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        if len(payload) >= 2:
            return _number(payload[0]), _number(payload[1])
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        text = payload.strip()
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if decoded is not None and decoded != payload:
            parsed = parse_cursor_response(decoded)
            if parsed is not None:
                return parsed
        numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        if len(numbers) >= 2:
            return float(numbers[0]), float(numbers[1])
    return None


def xdg_runtime_dir() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    if value:
        return Path(value)
    return Path(f"/run/user/{os.getuid()}")


def _is_socket(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        return False


def hyprland_socket_candidates(runtime: Path | None = None) -> list[Path]:
    runtime = runtime or xdg_runtime_dir()
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    candidates: list[Path] = []
    if signature:
        candidates.extend(
            (
                runtime / "hypr" / signature / ".socket.sock",
                Path("/tmp/hypr") / signature / ".socket.sock",
            )
        )
    candidates.extend((runtime / "hypr").glob("*/.socket.sock"))
    candidates.extend(Path("/tmp/hypr").glob("*/.socket.sock"))

    unique: dict[str, Path] = {}
    for candidate in candidates:
        if _is_socket(candidate):
            unique[str(candidate)] = candidate
    return sorted(
        unique.values(),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def bootstrap_hyprland_environment(runtime: Path | None = None) -> None:
    """Recover compositor variables for user services started before Hyprland."""

    runtime = runtime or xdg_runtime_dir()
    os.environ.setdefault("GDK_BACKEND", "wayland")
    os.environ.setdefault("XDG_SESSION_TYPE", "wayland")

    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        candidates = hyprland_socket_candidates(runtime)
        if candidates:
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = candidates[0].parent.name

    if not os.environ.get("WAYLAND_DISPLAY"):
        wayland_sockets = [
            path
            for path in runtime.glob("wayland-*")
            if not path.name.endswith(".lock") and _is_socket(path)
        ]
        if wayland_sockets:
            newest = max(wayland_sockets, key=lambda path: path.stat().st_mtime_ns)
            os.environ["WAYLAND_DISPLAY"] = newest.name


class HyprlandIPC:
    """Small socket1 client; unlike spawning hyprctl, it is cheap enough for pointers."""

    def __init__(self, runtime: Path | None = None, timeout: float = 0.20) -> None:
        self.runtime = runtime or xdg_runtime_dir()
        self.timeout = timeout
        self._socket_path: Path | None = None
        self._lock = threading.Lock()

    @property
    def socket_path(self) -> Path | None:
        if self._socket_path is None or not _is_socket(self._socket_path):
            candidates = hyprland_socket_candidates(self.runtime)
            self._socket_path = candidates[0] if candidates else None
        return self._socket_path

    def request(self, command: str, *, json_output: bool = True) -> Any:
        path = self.socket_path
        if path is None:
            return None
        prefix = "j/" if json_output else "/"
        message = f"{prefix}{command}".encode("utf-8")

        with self._lock:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(self.timeout)
                    connection.connect(str(path))
                    connection.sendall(message)
                    try:
                        connection.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    chunks: list[bytes] = []
                    while True:
                        try:
                            chunk = connection.recv(65536)
                        except socket.timeout:
                            break
                        if not chunk:
                            break
                        chunks.append(chunk)
            except OSError:
                self._socket_path = None
                return None

        raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
        if not json_output:
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw or None

    def cursor_position(self) -> tuple[float, float] | None:
        return parse_cursor_response(self.request("cursorpos", json_output=True))


class ConfigStore:
    """A tiny, atomic XDG config store shared by the service and controller."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            config_home = Path(
                os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            )
            path = config_home / "deskworlds" / "config.json"
        self.path = path

    def load(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        try:
            decoded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(decoded, dict):
                data = decoded
        except (OSError, json.JSONDecodeError):
            pass

        config = dict(DEFAULT_CONFIG)
        config.update(data)
        if config.get("world") not in WORLD_PAGES:
            config["world"] = DEFAULT_CONFIG["world"]
        config["paused"] = bool(config.get("paused", False))
        for key in ("max_fps", "covered_fps", "battery_fps", "pointer_fps"):
            try:
                value = int(config.get(key, DEFAULT_CONFIG[key]))
            except (TypeError, ValueError):
                value = int(DEFAULT_CONFIG[key])
            config[key] = max(1, min(120, value))
        return config

    def save(self, config: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(dict(config), indent=2, sort_keys=True) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="config.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def running_on_battery(sysfs: Path = Path("/sys/class/power_supply")) -> bool:
    """Read Linux power-supply state without starting a helper process."""

    try:
        supplies = list(sysfs.iterdir())
    except OSError:
        return False

    has_battery = False
    external_online: list[bool] = []
    for supply in supplies:
        try:
            kind = (supply / "type").read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        if kind == "battery":
            has_battery = True
        elif kind in {"mains", "usb", "usb_c", "usb-pd", "wireless"}:
            try:
                external_online.append(
                    (supply / "online").read_text(encoding="utf-8").strip() == "1"
                )
            except OSError:
                continue
    return has_battery and not any(external_online)
