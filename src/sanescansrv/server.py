#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Scanner Web Server - Website to talk to scanimage

"""Scanner Web Server - Website to talk to scanimage
Copyright (C) 2022  CoolCat467

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>."""

from __future__ import annotations

__title__ = "Sane Scanner Web Server"
__author__ = "CoolCat467"
__version__ = "2.0.0"


import socket
import sys
import time
from configparser import ConfigParser
from os import makedirs, path
from pathlib import Path
from typing import Any, AsyncIterator, Final
from urllib.parse import urlencode

import sane
import trio
from hypercorn.config import Config
from hypercorn.trio import serve
from jinja2 import Template
from quart import Response, request
from quart.templating import render_template as quart_render_template
from quart.templating import stream_template as quart_stream_template
from quart_trio import QuartTrio
from werkzeug import Response as wkresp

from sanescansrv import htmlgen

SaneError = sane._sane.error


def log(message: str, level: int = 1, log_dir: str | None = None) -> None:
    "Log a message to console and log file."
    levels = ["DEBUG", "INFO", "ERROR"]

    if log_dir is None:
        # log_dir = path.join(path.dirname(__file__), "logs")
        log_dir = path.abspath(
            path.expanduser(path.join("~", ".sanescansrv", "logs"))
        )
    if not path.exists(log_dir):
        makedirs(log_dir, exist_ok=True)
    filename = time.strftime("log_%Y_%m_%d.log")
    log_file = path.join(log_dir, filename)

    log_level = levels[min(max(0, level), len(levels) - 1)]
    log_time = time.asctime()
    log_message_text = message.encode("unicode_escape").decode("utf-8")

    log_msg = f"[{__title__}] [{log_time}] [{log_level}] {log_message_text}"

    if not path.exists(log_file):
        with open(log_file, mode="w", encoding="utf-8") as file:
            file.close()
    with open(log_file, mode="a", encoding="utf-8") as file:
        file.write(f"{log_msg}\n")
        file.close()
    print(log_msg)


# Stolen from WOOF (Web Offer One File), Copyright (C) 2004-2009 Simon Budig,
# avalable at http://www.home.unix-ag.org/simon/woof
# with modifications

# Utility function to guess the IP (as a string) where the server can be
# reached from the outside. Quite nasty problem actually.


def find_ip() -> str:
    """Guess the IP where the server can be found from the network"""
    # we get a UDP-socket for the TEST-networks reserved by IANA.
    # It is highly unlikely, that there is special routing used
    # for these networks, hence the socket later should give us
    # the IP address of the default route.
    # We're doing multiple tests, to guard against the computer being
    # part of a test installation.

    candidates: list[str] = []
    for test_ip in ("192.0.2.0", "198.51.100.0", "203.0.113.0"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((test_ip, 80))
        ip_addr: str = sock.getsockname()[0]
        sock.close()
        if ip_addr in candidates:
            return ip_addr
        candidates.append(ip_addr)

    return candidates[0]


def get_devices() -> dict[str, str]:
    "Return dict of SANE name to device"
    # Model name : Device
    devices: dict[str, str] = {}
    for device in sane.get_devices():
        devices[device[2]] = device[0]
    return devices


class DeviceSetting:
    "Setting for device"
    __slots__ = ("name", "title", "options", "default", "unit", "desc", "set")

    def __init__(
        self,
        name: str,
        title: str,
        options: list[str],
        default: str,
        unit: str,
        desc: str,
    ) -> None:
        self.name = name
        self.title = title
        self.options = options
        self.default = default
        self.desc = desc
        self.set = self.default

    def as_argument(self) -> str:
        "Return setting as argument"
        return f"--{self.name}={self.set}"

    def __repr__(self) -> str:
        return (
            f"DeviceSetting({self.name!r}, {self.title!r}, "
            f"{self.options!r}, {self.default!r}, {self.desc!r})"
        )


app: Final = QuartTrio(
    __name__,
    static_folder="static",
    template_folder="templates",
)  # pylint: disable=invalid-name
app_storage: Final[dict[str, Any]] = {}  # pylint: disable=invalid-name


async def render_template(
    template_name_or_list: str | list[str], **context: Any
) -> str:
    """Render the template with the context given.

    Arguments:
        template_name_or_list: Template name to render of a list of
            possible template names.
        context: The variables to pass to the template.

    Patched to remove blank lines left by jinja statements"""
    content = await quart_render_template(template_name_or_list, **context)
    new_content = []
    for line in content.splitlines():
        new_line = line.rstrip()
        if not new_line:
            continue
        new_content.append(new_line)
    return "\n".join(new_content)


async def stream_template(
    template_name_or_list: str | Template | list[str | Template],
    **context: Any,
) -> AsyncIterator[str]:
    """Render a template by name with the given context as a stream.

    This returns an iterator of strings, which can be used as a
    streaming response from a view.

    Arguments:
        template_name_or_list: The name of the template to render. If a
            list is given, the first name to exist will be rendered.
        context: The variables to make available in the template.

    Patched to remove blank lines left by jinja statements"""
    # Generate stream in this async block before context is lost
    stream = await quart_stream_template(template_name_or_list, **context)

    # Create async generator filter
    async def generate() -> AsyncIterator[str]:
        async for chunk in stream:
            for line in chunk.splitlines(True):
                if not line.rstrip():
                    continue
                yield line

    return generate()


def get_device_settings(device_addr: str) -> list[DeviceSetting]:
    "Get device settings."
    settings: list[DeviceSetting] = []

    try:
        device = sane.open(device_addr)
    except SaneError:
        return []

    for result in device.get_options():
        if not result[1] or "button" in result[1]:
            continue
        option = sane.Option(result, device)
        if not option.is_settable():
            continue

        constraints: list[str]
        type_ = sane.TYPE_STR[option.type].removeprefix("TYPE_")
        if isinstance(option.constraint, tuple):
            if isinstance(option.constraint[0], float):
                continue
            if option.constraint[2] == 0:
                continue
            range_ = range(*option.constraint)
            if len(range_) > 5:
                continue
            constraints = [str(i) for i in range_]
        if type_ not in {"INT", "STRING", "BOOL"}:
            continue
        if type_ == "BOOL":
            constraints = ["1", "0"]
        elif option.constraint is None:
            continue
        else:
            constraints = [str(x) for x in option.constraint]
        if len(constraints) < 2:
            continue

        default = "None"
        try:
            default = str(getattr(device, option.py_name))
        except Exception:
            pass
        unit = sane.UNIT_STR[option.unit].removeprefix("UNIT_")

        settings.append(
            DeviceSetting(
                option.name,
                option.title,
                constraints,
                default,
                unit,
                option.desc,
            )
        )

    device.close()
    return settings


def preform_scan(device_name: str, out_type: str = "png") -> str:
    "Scan using device and return path."
    if out_type not in {"pnm", "tiff", "png", "jpeg"}:
        raise ValueError("Output type must be pnm, tiff, png, or jpeg")
    filename = f"scan.{out_type}"
    assert app.static_folder is not None
    filepath = app.static_folder / filename

    ints = {"TYPE_BOOL", "TYPE_INT"}

    with sane.open(device_name) as device:
        for setting in app_storage["device_settings"][device_name]:
            name = setting.name.replace("-", "_")
            value: str | int = setting.set
            if sane.TYPE_STR[device[name].type] in ints:
                assert isinstance(value, str)
                if not value.isdigit():
                    continue
                value = int(value)
            setattr(device, name, value)
        with device.scan(print) as image:
            # bounds = image.getbbox()
            image.save(filepath, out_type)

    return filename


@app.get("/")
async def root_get() -> AsyncIterator[str]:
    "Main page get request"
    scanners = {}
    default = "none"

    if app_storage["scanners"]:
        scanners = {k: k for k in app_storage["scanners"]}
        # Since radio_select_dict is if comparison for
        # default, if default device does not exist
        # there simply won't be a default shown.
        default = app_storage["default_device"]

    return await stream_template(
        "root_get.html.jinja",
        scanners=scanners,
        default=default,
    )


@app.post("/")
async def root_post() -> Response | wkresp:
    "Main page post handling"
    multi_dict = await request.form
    data = multi_dict.to_dict()

    # Validate input
    img_format = data.get("img_format", "png")
    device = app_storage["scanners"].get(data.get("scanner"), "none")

    if img_format not in {"pnm", "tiff", "png", "jpeg"}:
        return app.redirect("/")
    if device == "none":
        return app.redirect("/scanners")

    filename = preform_scan(device, img_format)

    return await app.send_static_file(filename)


@app.get("/update_scanners")
async def update_scanners_get() -> wkresp:
    "Update scanners get handling"
    app_storage["scanners"] = get_devices()
    for device in app_storage["scanners"].values():
        app_storage["device_settings"][device] = get_device_settings(device)
    return app.redirect("scanners")


@app.get("/scanners")
async def scanners_get() -> AsyncIterator[str]:
    "Scanners page get handling"
    scanners = {}
    for display in app_storage.get("scanners", {}):
        scanner_url = urlencode({"scanner": display})
        scanners[f"/settings?{scanner_url}"] = display

    return await stream_template(
        "scanners_get.html.jinja",
        scanners=scanners,
    )


def get_setting_radio(setting: DeviceSetting) -> str:
    "Return setting radio section"
    options = {x.title(): x for x in setting.options}
    if set(options.keys()) == {"1", "0"}:
        options = {"True": "1", "False": "0"}
    return htmlgen.radio_select_box(
        setting.name, options, setting.set, f"{setting.title} - {setting.desc}"
    )


@app.get("/settings")
async def settings_get() -> AsyncIterator[str] | wkresp:
    "Settings page get handling"
    scanner = request.args.get("scanner", "none")

    if scanner == "none" or scanner not in app_storage["scanners"]:
        return app.redirect("/scanners")

    device = app_storage["scanners"][scanner]
    scanner_settings = app_storage["device_settings"].get(device, [])

    return await stream_template(
        "settings_get.html.jinja",
        scanner=scanner,
        radios="\n".join(
            get_setting_radio(setting) for setting in scanner_settings
        ),
    )


@app.post("/settings")
async def settings_post() -> wkresp:
    "Settings page post handling"
    scanner = request.args.get("scanner", "none")

    if scanner == "none" or scanner not in app_storage["scanners"]:
        return app.redirect("/scanners")

    device = app_storage["scanners"][scanner]
    scanner_settings = app_storage["device_settings"][device]

    valid_settings = {
        setting.name: idx for idx, setting in enumerate(scanner_settings)
    }

    multi_dict = await request.form
    data = multi_dict.to_dict()

    for setting_name, new_value in data.items():
        # Input validation
        if setting_name not in valid_settings:
            continue
        idx = valid_settings[setting_name]
        if new_value not in scanner_settings[idx].options:
            continue
        app_storage["device_settings"][device][idx].set = new_value

    # Return to page for that scanner
    return app.redirect(request.url)


async def serve_scanner(
    root_dir: str,
    device_name: str,
    port: int,
    *,
    ip_addr: str | None = None,
) -> None:
    """Asynchronous Entry Point"""
    if not ip_addr:
        ip_addr = find_ip()

    try:
        # Add more information about the address
        location = f"{ip_addr}:{port}"

        config = {
            "bind": [location],
            "worker_class": "trio",
            "errorlog": path.join(
                root_dir, "logs", time.strftime("log_%Y_%m_%d.log")
            ),
        }
        app.config["SERVER_NAME"] = location

        app.static_folder = Path(root_dir, "static")

        app.add_url_rule(
            "/",
            "static",
            app.send_static_file,
            defaults={"filename": "index.html"},
        )
        app.add_url_rule("/<path:filename>", "static", app.send_static_file)

        config_obj = Config.from_mapping(config)

        app_storage["scanners"] = {}
        app_storage["default_device"] = device_name
        app_storage["device_settings"] = {}

        print(f"Serving on http://{location}\n(CTRL + C to quit)")

        await serve(app, config_obj)
    except socket.error:
        log(f"Cannot bind to IP address '{ip_addr}' port {port}", 2)
        sys.exit(1)
    except KeyboardInterrupt:
        log("Shutting down from keyboard interrupt")


def run() -> None:
    "Run scanner server"
    root_dir = path.abspath(path.expanduser(path.join("~", ".sanescansrv")))
    if not path.exists(root_dir):
        makedirs(root_dir, exist_ok=True)

    config = ConfigParser()
    conf_file = path.join(root_dir, "config.ini")
    config.read(conf_file)

    target = "None"
    port = 3004

    rewrite = True
    if config.has_section("main"):
        rewrite = False
        if config.has_option("main", "printer"):
            target = config.get("main", "printer")
        else:
            rewrite = True
        if config.has_option("main", "port"):
            raw = config.get("main", "port")
            rewrite = True
            if raw.isdigit():
                port = int(raw)
                rewrite = False
        else:
            rewrite = True

    if rewrite:
        config.clear()
        config.read_dict({"main": {"printer": target, "port": port}})
        with open(conf_file, "w", encoding="utf-8") as config_file:
            config.write(config_file)

    print(f"Default Printer: {target}\nPort: {port}\n")

    if target == "None":
        print("No default device in config file.")

    trio.run(serve_scanner, root_dir, target, port)


def sane_run() -> None:
    """Run but also handle initializing and uninitializing SANE"""
    try:
        sane.init()
        run()
    finally:
        sane.exit()


if __name__ == "__main__":
    sane_run()
