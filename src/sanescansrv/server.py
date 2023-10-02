"""Scanner Web Server - Website to talk to SANE scanners.

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
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from __future__ import annotations

__title__ = "Sane Scanner Web Server"
__author__ = "CoolCat467"
__version__ = "2.1.3"
__license__ = "GPLv3"


import contextlib
import socket
import sys
import time
from collections.abc import AsyncIterator
from configparser import ConfigParser
from dataclasses import dataclass
from functools import partial
from os import makedirs, path
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlencode

import sane
import trio
from hypercorn.config import Config
from hypercorn.trio import serve
from quart import Response, request, request_started
from quart.ctx import RequestContext
from quart.templating import stream_template
from quart_trio import QuartTrio
from werkzeug import Response as WerkzeugResponse

from sanescansrv import htmlgen, logger
from sanescansrv.logger import log

# For some reason error class is not exposed nicely; Let's fix that
SaneError = sane._sane.error
logger.set_title(__title__)

SANE_INITIALIZED = False


def stop_sane() -> None:
    """Exit SANE if started while also updating SANE_INITIALIZED global."""
    global SANE_INITIALIZED
    if SANE_INITIALIZED:
        sane.exit()
    SANE_INITIALIZED = False


def restart_sane() -> None:
    """Start or restart SANE."""
    global SANE_INITIALIZED
    stop_sane()
    sane.init()
    SANE_INITIALIZED = True


# Stolen from WOOF (Web Offer One File), Copyright (C) 2004-2009 Simon Budig,
# available at http://www.home.unix-ag.org/simon/woof
# with modifications

# Utility function to guess the IP (as a string) where the server can be
# reached from the outside. Quite nasty problem actually.


def find_ip() -> str:
    """Guess the IP where the server can be found from the network."""
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
    """Return dict of SANE name to device."""
    restart_sane()
    # Model name : Device
    devices: dict[str, str] = {}
    for device_name, _vendor, model, _type in sane.get_devices(localOnly=True):
        devices[model] = device_name
    return devices


@dataclass
class DeviceSetting:
    """Setting for device."""

    name: str
    title: str
    options: list[str]
    default: str
    unit: str
    desc: str
    set: str | None = None  # noqa: A003  # Attribute shadows builtin

    def as_argument(self) -> str:
        """Return setting as argument."""
        return f"--{self.name}={self.set if self.set is not None else self.default}"


class HopefullyTemporarySubclass(QuartTrio):
    """Hopefully Temporary Subclass of QuartTrio with patched code because QuartTrio is out of date."""

    __slots__ = ()

    async def full_dispatch_request(
        self,
        request_context: RequestContext | None = None,
    ) -> Response | WerkzeugResponse:
        """Adds pre and post processing to the request dispatching.

        Arguments:
        ---------
            request_context: The request context, optional as Flask
                omits this argument.
        """
        # await self.try_trigger_before_first_request_functions()
        # await request_started.send(self)
        await request_started.send_async(self, _sync_wrapper=self.ensure_async)
        try:
            result = await self.preprocess_request(request_context)
            if result is None:
                result = await self.dispatch_request(request_context)
        except (Exception, trio.MultiError) as error:
            result = await self.handle_user_exception(error)  # type: ignore[assignment]
        return await self.finalize_request(result, request_context)  # type: ignore[arg-type]


app: Final = HopefullyTemporarySubclass(  # pylint: disable=invalid-name
    __name__,
    static_folder="static",
    template_folder="templates",
)
APP_STORAGE: Final[dict[str, Any]] = {}


def get_device_settings(device_addr: str) -> list[DeviceSetting]:
    """Get device settings."""
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
        with contextlib.suppress(AttributeError, ValueError):
            default = str(getattr(device, option.py_name))

        unit = sane.UNIT_STR[option.unit].removeprefix("UNIT_")

        settings.append(
            DeviceSetting(
                option.name,
                option.title,
                constraints,
                default,
                unit,
                option.desc,
            ),
        )

    device.close()
    return settings


def display_progress(current: int, total: int) -> None:
    """Display progress of the active scan."""
    print(f"{current / total * 100:.2f}%")


def preform_scan(device_name: str, out_type: str = "png") -> str:
    """Scan using device and return path."""
    if out_type not in {"pnm", "tiff", "png", "jpeg"}:
        raise ValueError("Output type must be pnm, tiff, png, or jpeg")
    filename = f"scan.{out_type}"
    assert app.static_folder is not None
    filepath = Path(app.static_folder) / filename

    ints = {"TYPE_BOOL", "TYPE_INT"}

    with sane.open(device_name) as device:
        for setting in APP_STORAGE["device_settings"][device_name]:
            name = setting.name.replace("-", "_")
            value: str | int = setting.set
            if sane.TYPE_STR[device[name].type] in ints:
                assert isinstance(value, str)
                if not value.isdigit():
                    continue
                value = int(value)
            setattr(device, name, value)
        with device.scan(display_progress) as image:
            # bounds = image.getbbox()
            image.save(filepath, out_type)

    return filename


@app.get("/")  # type: ignore[type-var]
async def root_get() -> AsyncIterator[str]:
    """Main page get request."""
    scanners = {}
    default = "none"

    if APP_STORAGE["scanners"]:
        scanners = {k: k for k in APP_STORAGE["scanners"]}
        # Since radio_select_dict is if comparison for
        # default, if default device does not exist
        # there simply won't be a default shown.
        default = APP_STORAGE["default_device"]

    return await stream_template(
        "root_get.html.jinja",
        scanners=scanners,
        default=default,
    )


@app.post("/")
async def root_post() -> Response | WerkzeugResponse:
    """Main page post handling."""
    multi_dict = await request.form
    data = multi_dict.to_dict()

    # Validate input
    img_format = data.get("img_format", "png")
    device = APP_STORAGE["scanners"].get(data.get("scanner"), "none")

    if img_format not in {"pnm", "tiff", "png", "jpeg"}:
        return app.redirect("/")
    if device == "none":
        return app.redirect("/scanners")

    filename = preform_scan(device, img_format)

    return app.redirect(f"/{filename}")


@app.get("/update_scanners")
async def update_scanners_get() -> WerkzeugResponse:
    """Update scanners get handling."""
    APP_STORAGE["scanners"] = get_devices()
    for device in APP_STORAGE["scanners"].values():
        APP_STORAGE["device_settings"][device] = get_device_settings(device)
    return app.redirect("scanners")


@app.get("/scanners")  # type: ignore[type-var]
async def scanners_get() -> AsyncIterator[str]:
    """Scanners page get handling."""
    scanners = {}
    for display in APP_STORAGE.get("scanners", {}):
        scanner_url = urlencode({"scanner": display})
        scanners[f"/settings?{scanner_url}"] = display

    return await stream_template(
        "scanners_get.html.jinja",
        scanners=scanners,
    )


def get_setting_radio(setting: DeviceSetting) -> str:
    """Return setting radio section."""
    options = {x.title(): x for x in setting.options}
    if set(options.keys()) == {"1", "0"}:
        options = {"True": "1", "False": "0"}
    return htmlgen.radio_select_box(
        setting.name,
        options,
        setting.set,
        f"{setting.title} - {setting.desc}",
    )


@app.get("/settings")  # type: ignore[type-var]
async def settings_get() -> AsyncIterator[str] | WerkzeugResponse:
    """Settings page get handling."""
    scanner = request.args.get("scanner", "none")

    if scanner == "none" or scanner not in APP_STORAGE["scanners"]:
        return app.redirect("/scanners")

    device = APP_STORAGE["scanners"][scanner]
    scanner_settings = APP_STORAGE["device_settings"].get(device, [])

    return await stream_template(
        "settings_get.html.jinja",
        scanner=scanner,
        radios="\n".join(
            get_setting_radio(setting) for setting in scanner_settings
        ),
    )


@app.post("/settings")
async def settings_post() -> WerkzeugResponse:
    """Settings page post handling."""
    scanner = request.args.get("scanner", "none")

    if scanner == "none" or scanner not in APP_STORAGE["scanners"]:
        return app.redirect("/scanners")

    device = APP_STORAGE["scanners"][scanner]
    scanner_settings = APP_STORAGE["device_settings"][device]

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
        APP_STORAGE["device_settings"][device][idx].set = new_value

    # Return to page for that scanner
    return app.redirect(request.url)


async def serve_scanner(
    root_dir: str,
    device_name: str,
    port: int,
    *,
    ip_addr: str | None = None,
) -> None:
    """Asynchronous Entry Point."""
    if not ip_addr:
        ip_addr = find_ip()

    try:
        # Add more information about the address
        location = f"{ip_addr}:{port}"

        config = {
            "bind": [location],
            "worker_class": "trio",
            "errorlog": path.join(
                root_dir,
                "logs",
                time.strftime("log_%Y_%m_%d.log"),
            ),
        }
        app.config["SERVER_NAME"] = location
        app.config["EXPLAIN_TEMPLATE_LOADING"] = False

        app.jinja_options = {
            "trim_blocks": True,
            "lstrip_blocks": True,
        }

        app.add_url_rule("/<path:filename>", "static", app.send_static_file)

        config_obj = Config.from_mapping(config)

        APP_STORAGE["scanners"] = {}
        APP_STORAGE["default_device"] = device_name
        APP_STORAGE["device_settings"] = {}

        print(f"Serving on http://{location}\n(CTRL + C to quit)")

        await serve(app, config_obj)
    except OSError:
        log(f"Cannot bind to IP address '{ip_addr}' port {port}", 2)
        sys.exit(1)
    except KeyboardInterrupt:
        log("Shutting down from keyboard interrupt")


def run() -> None:
    """Run scanner server."""
    root_dir = path.abspath(path.expanduser(path.join("~", ".sanescansrv")))
    if not path.exists(root_dir):
        makedirs(root_dir, exist_ok=True)

    config = ConfigParser()
    conf_file = path.join(root_dir, "config.ini")
    config.read(conf_file)

    target = "None"
    port = 3004
    hostname = "None"

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
        if config.has_option("main", "hostname"):
            hostname = config.get("main", "hostname")
        else:
            rewrite = True

    if rewrite:
        config.clear()
        config.read_dict(
            {
                "main": {
                    "printer": target,
                    "port": port,
                    "hostname": hostname,
                },
            },
        )
        with open(conf_file, "w", encoding="utf-8") as config_file:
            config.write(config_file)

    print(f"Default Printer: {target}\nPort: {port}\nHostname: {hostname}\n")

    if target == "None":
        print("No default device in config file.")

    ip_address = None
    if hostname != "None":
        ip_address = hostname

    trio.run(
        partial(serve_scanner, root_dir, target, port, ip_addr=ip_address),
    )


def sane_run() -> None:
    """Run but also handle initializing and un-initializing SANE."""
    try:
        run()
    finally:
        stop_sane()


if __name__ == "__main__":
    sane_run()
