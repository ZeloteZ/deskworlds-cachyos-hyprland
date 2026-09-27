#!/usr/bin/env python3
"""Command-line controller for the Deskworlds Hyprland host."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any


WORLD_ALIASES = {
    "riverbed": "riverscape",
    "river": "riverscape",
    "riverscape": "riverscape",
    "coral-reef": "reefscape",
    "coral": "reefscape",
    "reef": "reefscape",
    "reefscape": "reefscape",
    "betta": "bettascape",
    "bettascape": "bettascape",
}


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))


def send_command(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    message = (json.dumps(payload) + "\n").encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5.0)
            connection.connect(str(path))
            connection.sendall(message)
            chunks: list[bytes] = []
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
    except FileNotFoundError as error:
        raise RuntimeError(
            "Deskworlds is not running (control socket does not exist)."
        ) from error
    except ConnectionRefusedError as error:
        raise RuntimeError(
            "Deskworlds left a stale control socket; restart the user service."
        ) from error
    except socket.timeout as error:
        raise RuntimeError("Deskworlds did not answer within five seconds.") from error
    except OSError as error:
        raise RuntimeError(f"could not contact Deskworlds: {error}") from error

    raw = b"".join(chunks).split(b"\n", 1)[0]
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Deskworlds returned an invalid response.") from error
    if not isinstance(response, dict):
        raise RuntimeError("Deskworlds returned an invalid response.")
    return response


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="deskworldsctl",
        description="Control the Deskworlds live Hyprland wallpaper.",
    )
    result.add_argument(
        "--socket",
        type=Path,
        default=runtime_dir() / "deskworlds" / "control.sock",
        help="override the control socket path",
    )
    subcommands = result.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status", help="show world, outputs, and render policy")
    subcommands.add_parser("pause", help="stop rendering and remember the choice")
    subcommands.add_parser("resume", help="resume rendering")
    subcommands.add_parser("toggle", help="toggle pause/resume")
    subcommands.add_parser("feed", help="drop food into every running world")
    subcommands.add_parser("reload", help="recreate every output surface")
    world = subcommands.add_parser("world", help="switch the world on every output")
    world.add_argument(
        "name",
        help="riverbed, coral-reef, or betta (internal *scape names also work)",
    )
    subcommands.add_parser("quit", help="stop the running host")
    return result


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    request: dict[str, Any] = {"command": args.command}
    if args.command == "world":
        key = args.name.strip().lower().replace("_", "-").replace(" ", "-")
        world = WORLD_ALIASES.get(key)
        if world is None:
            raise ValueError("unknown world; choose riverbed, coral-reef, or betta")
        request["value"] = world
    return request


def print_status(status: dict[str, Any]) -> None:
    state = "paused" if status.get("paused") else "running"
    power = "battery" if status.get("on_battery") else "external power"
    print(f"{status.get('world_title', status.get('world'))}: {state}, {power}")
    socket_path = status.get("hyprland_socket")
    print(f"Hyprland IPC: {socket_path or 'unavailable'}")
    outputs = status.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        print("Outputs: none")
        return
    print("Outputs:")
    for output in outputs:
        if not isinstance(output, dict):
            continue
        name = output.get("hyprland_name") or output.get("name") or "unknown"
        fps = output.get("fps", 0)
        exposure = float(output.get("exposure", 0.0)) * 100
        loaded = "ready" if output.get("loaded") else "loading"
        print(f"  {name}: {fps} fps, {exposure:.0f}% exposed, {loaded}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        request = build_request(args)
        response = send_command(args.socket, request)
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error", "command failed")))
        status = response.get("status")
        if isinstance(status, dict):
            print_status(status)
        elif response.get("message"):
            print(response["message"])
    except (RuntimeError, ValueError) as error:
        print(f"deskworldsctl: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
