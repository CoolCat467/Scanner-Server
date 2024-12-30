"""Scanner Web Server - Website to talk to SANE scanners.

Copyright (C) 2022-2024  CoolCat467

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

__title__ = "Sane Scanner Webserver"
__author__ = "CoolCat467"
__version__ = "3.1.0"
__license__ = "GNU General Public License Version 3"


import contextlib
import functools
import math
import socket
import statistics
import sys
import tempfile
import time
import traceback
import uuid
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
)
from dataclasses import dataclass
from enum import IntEnum, auto
from os import getenv, makedirs, path
from pathlib import Path
from shutil import rmtree
from typing import TYPE_CHECKING, Any, Final, NamedTuple, TypeVar
from urllib.parse import urlencode

import sane
import trio
from hypercorn.config import Config
from hypercorn.trio import serve
from PIL import Image
from quart import request, send_file
from quart.templating import stream_template
from quart_trio import QuartTrio
from werkzeug.exceptions import HTTPException

from sanescansrv import elapsed, htmlgen, logger
from sanescansrv.logger import log

if sys.version_info < (3, 11):
    import tomli as tomllib
    from exceptiongroup import BaseExceptionGroup
else:
    import tomllib

if TYPE_CHECKING:
    from quart.wrappers.response import Response as QuartResponse
    from typing_extensions import ParamSpec
    from werkzeug import Response as WerkzeugResponse

    PS = ParamSpec("PS")

HOME: Final = trio.Path(getenv("HOME", path.expanduser("~")))
XDG_DATA_HOME: Final = trio.Path(
    getenv("XDG_DATA_HOME", HOME / ".local" / "share"),
)
XDG_CONFIG_HOME: Final = trio.Path(getenv("XDG_CONFIG_HOME", HOME / ".config"))

FILE_TITLE: Final = __title__.lower().replace(" ", "-").replace("-", "_")
CONFIG_PATH: Final = XDG_CONFIG_HOME / FILE_TITLE
DATA_PATH: Final = XDG_DATA_HOME / FILE_TITLE
MAIN_CONFIG: Final = CONFIG_PATH / "config.toml"
TEMP_PATH = Path(tempfile.mkdtemp(suffix="_sane_scan_srv"))

# For some reason error class is not exposed nicely; Let's fix that
SaneError: Final = sane._sane.error
logger.set_title(__title__)

SANE_INITIALIZED = False

T = TypeVar("T")


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


def combine_end(data: Iterable[str], final: str = "and") -> str:
    """Return comma separated string of list of strings with last item phrased properly."""
    data = list(data)
    if len(data) >= 2:
        data[-1] = f"{final} {data[-1]}"
    if len(data) > 2:
        return ", ".join(data)
    return " ".join(data)


async def send_error(
    page_title: str,
    error_body: str,
    return_link: str | None = None,
) -> AsyncIterator[str]:
    """Stream error page."""
    return await stream_template(
        "error_page.html.jinja",
        page_title=page_title,
        error_body=error_body,
        return_link=return_link,
    )


async def get_exception_page(
    code: int,
    name: str,
    desc: str,
    return_link: str | None = None,
) -> tuple[AsyncIterator[str], int]:
    """Return Response for exception."""
    resp_body = await send_error(
        page_title=f"{code} {name}",
        error_body=desc,
        return_link=return_link,
    )
    return (resp_body, code)


def pretty_exception_name(exc: BaseException) -> str:
    """Make exception into pretty text (split by spaces)."""
    exc_str, reason = repr(exc).split("(", 1)
    reason = reason[1:-2]
    words = []
    last = 0
    for idx, char in enumerate(exc_str):
        if char.islower():
            continue
        word = exc_str[last:idx]
        if not word:
            continue
        words.append(word)
        last = idx
    words.append(exc_str[last:])
    error = " ".join(w for w in words if w not in {"Error", "Exception"})
    return f"{error} ({reason})"


def pretty_exception(
    function: Callable[PS, Awaitable[T]],
) -> Callable[PS, Awaitable[T | tuple[AsyncIterator[str], int]]]:
    """Make exception pages pretty."""

    @functools.wraps(function)
    async def wrapper(  # type: ignore[misc]
        *args: PS.args,
        **kwargs: PS.kwargs,
    ) -> T | tuple[AsyncIterator[str], int]:
        code = 500
        name = "Exception"
        desc = (
            "The server encountered an internal error and "
            + "was unable to complete your request. "
            + "Either the server is overloaded or there is an error "
            + "in the application."
        )
        try:
            return await function(*args, **kwargs)
        except Exception as exception:
            # traceback.print_exception changed in 3.10
            if sys.version_info < (3, 10):
                tb = sys.exc_info()[2]
                traceback.print_exception(etype=None, value=exception, tb=tb)
            else:
                traceback.print_exception(exception)

            if isinstance(exception, HTTPException):
                code = exception.code or code
                desc = exception.description or desc
                name = exception.name or name
            else:
                exc_name = pretty_exception_name(exception)
                name = f"Internal Server Error ({exc_name})"

        return await get_exception_page(
            code,
            name,
            desc,
        )

    return wrapper


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
    options: list[str | int] | tuple[int | float, int | float, int | float]
    default: str
    unit: str
    desc: str
    option_type: str
    set: str | None = None
    usable: bool = True

    def as_argument(self) -> str:
        """Return setting as argument."""
        return f"--{self.name}={self.set if self.set is not None else self.default}"


app: Final = QuartTrio(  # pylint: disable=invalid-name
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
        # print(f"\n{result = }")
        if not result[1]:
            continue

        option = sane.Option(result, device)

        usable = True
        if not option.is_settable():
            # print("> Not settable")
            usable = False

        if not option.is_active():
            usable = False

        # Disable button control items for now (greyed out)
        if usable and "button" in option.name:
            usable = False

        constraints: (
            list[str | int] | tuple[int | float, int | float, int | float]
        ) = []
        if option.constraint is not None:
            constraints = option.constraint
            if (
                isinstance(option.constraint, tuple)
                and len(option.constraint) != 3
            ):
                usable = False
        type_ = sane.TYPE_STR[option.type].removeprefix("TYPE_")
        # print(f'{type_ = }')

        if type_ == "BOOL":
            constraints = [0, 1]

        option_type = type_

        default = "None"
        with contextlib.suppress(AttributeError, ValueError):
            default = str(getattr(device, option.py_name))
        # print(f'{default = }')

        unit = sane.UNIT_STR[option.unit].removeprefix("UNIT_")

        settings.append(
            DeviceSetting(
                name=option.name,
                title=option.title,
                options=constraints,
                default=default,
                unit=unit,
                desc=option.desc,
                option_type=option_type,
                usable=usable,
            ),
        )

    device.close()
    return settings


def display_progress(current: int, total: int) -> None:
    """Display progress of the active scan."""
    print(f"{current / total * 100:.2f}%")


def preform_scan(
    device_name: str,
    out_type: str = "png",
    progress: Callable[[int, int], object] = display_progress,
) -> str:
    """Scan using device and return path."""
    if out_type not in {"pnm", "tiff", "png", "jpeg"}:
        raise ValueError("Output type must be pnm, tiff, png, or jpeg")
    filename = f"{uuid.uuid4()!s}_scan.{out_type}"
    assert app.static_folder is not None
    if not TEMP_PATH.exists():
        makedirs(TEMP_PATH)
    filepath = TEMP_PATH / filename

    ints = {"TYPE_BOOL", "TYPE_INT"}
    float_ = "TYPE_FIXED"

    with sane.open(device_name) as device:
        for setting in APP_STORAGE["device_settings"][device_name]:
            name = setting.name.replace("-", "_")
            if setting.set is None:
                continue
            if not setting.usable:
                continue
            value: str | int | float = setting.set
            type_string = sane.TYPE_STR[device[name].type]
            if type_string == float_:
                assert isinstance(value, str), f"{value = } {type(value) = }"
                try:
                    value = float(value)
                except ValueError:
                    continue
            elif type_string in ints:
                assert isinstance(value, str), f"{value = } {type(value) = }"
                negative = value.startswith("-")
                value = value.removeprefix("-")
                if value.isdigit():
                    value = int(value)
                    if negative:
                        value *= -1
                options = setting.options
                if options and isinstance(options, tuple):
                    min_ = options[0]
                    # Skip autocalibration values
                    if min_ == -1 and value == -1:
                        continue
            try:
                setattr(device, name, value)
            except (AttributeError, TypeError) as exc:
                print(f"\n{name} = {value!r}")
                # traceback.print_exception changed in 3.10
                if sys.version_info < (3, 10):
                    tb = sys.exc_info()[2]
                    traceback.print_exception(etype=None, value=exc, tb=tb)
                else:
                    traceback.print_exception(exc)
                ## APP_STORAGE["device_settings"][device_name][idx].usable = False
        with device.scan(progress) as image:
            bounds = image.getbbox()
            if bounds is not None:
                image = image.crop(bounds)
            image.save(filepath, out_type)

    return filename


class ScanProgress(NamedTuple):
    """Scan Progress Data."""

    current: int
    total: int


class ScanStatus(IntEnum):
    """Scan Status Data."""

    STARTED = auto()
    IN_PROGRESS = auto()
    DONE = auto()
    ERROR = auto()


def fake_preform_scan(
    _device_name: str,
    _out_type: str = "png",
    progress: Callable[[int, int], object] = display_progress,
) -> str:
    """Perform fake scan."""
    total = 100
    for current in range(total):
        progress(current, total)
        time.sleep(0.05)
    return "favicon.ico"


SCAN_LOCK = trio.Lock()


async def preform_scan_async(
    device_name: str,
    out_type: str,
    task_status: trio.TaskStatus[Any] = trio.TASK_STATUS_IGNORED,
) -> str | None:
    """Scan using device and return path."""
    if out_type not in {"pnm", "tiff", "png", "jpeg"}:
        raise ValueError("Output type must be pnm, tiff, png, or jpeg")

    delays = []
    last_time = 0

    def progress(current: int, total: int) -> None:
        """Scan is in progress."""
        nonlocal last_time
        prev_last, last_time = last_time, time.perf_counter_ns()
        delays.append(last_time - prev_last)
        APP_STORAGE["scan_status"] = (
            ScanStatus.IN_PROGRESS,
            ScanProgress(current, total),
            delays,
        )

    async with SCAN_LOCK:
        APP_STORAGE["scan_status"] = (ScanStatus.STARTED,)
        task_status.started()
        last_time = time.perf_counter_ns()
        try:
            filename = await trio.to_thread.run_sync(
                preform_scan,  # fake_preform_scan,
                device_name,
                out_type,
                progress,
                thread_name="preform_scan_async",
            )
        except (SaneError, RuntimeError) as exc:
            # traceback.print_exception changed in 3.10
            if sys.version_info < (3, 10):
                tb = sys.exc_info()[2]
                traceback.print_exception(etype=None, value=exc, tb=tb)
            else:
                traceback.print_exception(exc)

            APP_STORAGE["scan_status"] = (
                ScanStatus.ERROR,
                exc,
            )
            return None
        ##except SaneError as ex:
        ##    if "Invalid argument" in ex.args:
        APP_STORAGE["scan_status"] = (
            ScanStatus.DONE,
            filename,
        )
    return filename


@app.get("/scan/<scan_filename>")  # type: ignore[type-var]
@pretty_exception
async def handle_scan_get(
    scan_filename: str,
) -> tuple[AsyncIterator[str], int] | QuartResponse:
    """Handle scan result page GET request."""
    temp_file = TEMP_PATH / scan_filename
    if not temp_file.exists():
        response_body = await send_error(
            page_title="404: Could Not Find Requested Scan.",
            error_body="Requested scan not found.",
        )
        return (response_body, 404)
    return await send_file(temp_file, attachment_filename=scan_filename)


@app.get("/scan-status")  # type: ignore[type-var]
@pretty_exception
async def scan_status_get() -> (
    AsyncIterator[str] | tuple[AsyncIterator[str], int] | WerkzeugResponse
):
    """Handle scan status GET request."""
    raw_status = APP_STORAGE.get("scan_status")
    if raw_status is None:
        return await get_exception_page(
            404,  # not found
            "No Scan Currently Running",
            "There are no scan requests running currently. "
            "Start one by pressing the `Scan!` button on the main page.",
        )
    assert raw_status is not None

    status, *data = raw_status

    if status == ScanStatus.ERROR:
        exception = data[0]
        name = pretty_exception_name(exception)
        return await get_exception_page(
            500,  # internal server error
            "Scan Error",
            "The following error occurred attempting to process the scan "
            f"request: {name!r} (See server console for more details).",
        )

    if status == ScanStatus.DONE:
        filename = data[0]
        return app.redirect(f"/scan/{filename}")

    progress: ScanProgress | None = None
    time_deltas_ns: list[int] | None = None
    delay = 5
    estimated_wait: int = 120

    if status == ScanStatus.STARTED:
        delay = 15

    if status == ScanStatus.IN_PROGRESS:
        progress, time_deltas_ns = data

        assert isinstance(progress, ScanProgress)
        assert isinstance(time_deltas_ns, list)

        # Estimate when the scan will be done
        # Nanoseconds
        average_wait_ns = statistics.mean(time_deltas_ns)
        delta_total = progress.total - progress.current
        estimated_wait_ns = delta_total * average_wait_ns
        # nanoseconds -> seconds
        estimated_wait = math.ceil(estimated_wait_ns // 1e9)
        delay = max(delay, min(10, estimated_wait))

    return await stream_template(
        "scan-status_get.html.jinja",
        just_started=status == ScanStatus.STARTED,
        progress=progress,
        estimated_wait=elapsed.get_elapsed(estimated_wait) or "0 seconds",
        refreshes_after=delay,
    )


@app.get("/")  # type: ignore[type-var]
async def root_get() -> AsyncIterator[str]:
    """Handle main page GET request."""
    scanners = {}
    default = "none"

    if APP_STORAGE["scanners"]:
        scanners = {k: k for k in APP_STORAGE["scanners"]}
        # Since radio_select_dict is if comparison for
        # default, if default device does not exist
        # there simply won't be a default shown.
        default = APP_STORAGE["default_device"]
        # If default not in scanners list,
        if default not in scanners.values():
            # Set default to first scanner
            default = sorted(scanners.values())[0]

    return await stream_template(
        "root_get.html.jinja",
        scanners=scanners,
        default=default,
    )


@app.post("/")  # type: ignore[type-var]
@pretty_exception
async def root_post() -> (
    WerkzeugResponse | AsyncIterator[str] | tuple[AsyncIterator[str], int]
):
    """Handle page POST."""
    multi_dict = await request.form
    data = multi_dict.to_dict()

    # Validate input
    img_format = data.get("img_format", "png")
    device = APP_STORAGE["scanners"].get(data.get("scanner"), "none")

    if img_format not in {"pnm", "tiff", "png", "jpeg"}:
        return app.redirect("/")
    if device == "none":
        return app.redirect("/scanners")

    raw_status = APP_STORAGE.get("scan_status")

    if raw_status is not None:
        status, *_data = raw_status
        if status not in {ScanStatus.ERROR, ScanStatus.DONE}:
            return await get_exception_page(
                403,  # forbidden
                "Scan Already Currently Running",
                "There is a scan request already running. Please wait for the previous scan to complete.",
                return_link="/scan-status",
            )
        APP_STORAGE["scan_status"] = None

    nursery: trio.Nursery | None = APP_STORAGE.get("nursery")
    assert isinstance(nursery, trio.Nursery), "Must be nursery"

    await nursery.start(preform_scan_async, device, img_format)

    return app.redirect("/scan-status")


def update_scanners() -> None:
    """Update scanners list."""
    APP_STORAGE["scanners"] = get_devices()
    for _model, device in APP_STORAGE["scanners"].items():
        if device not in APP_STORAGE["device_settings"]:
            APP_STORAGE["device_settings"][device] = get_device_settings(
                device,
            )


async def update_scanners_async() -> bool:
    """Update scanners list asynchronously. Return if successful."""
    if SCAN_LOCK.locked():
        return False
    async with SCAN_LOCK:
        await trio.to_thread.run_sync(update_scanners)
    return True


@app.get("/update_scanners")  # type: ignore[type-var]
@pretty_exception
async def update_scanners_get() -> (
    WerkzeugResponse | AsyncIterator[str] | tuple[AsyncIterator[str], int]
):
    """Update scanners get handling."""
    success = await update_scanners_async()
    if not success:
        return await get_exception_page(
            403,  # forbidden
            "Scan Currently Running",
            "There is a scan request currently running, updating the device list at this time might not be smart.",
            return_link="/update_scanners",
        )
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


def get_setting_radio(setting: DeviceSetting) -> str | None:
    """Return setting radio section."""
    box_title = f"{setting.title} - {setting.desc}"

    default = setting.default if setting.set is None else setting.set
    options: Mapping[str, str | dict[str, str]] = {}

    if setting.option_type == "BOOL":
        options = {"True": "1", "False": "0"}
    elif setting.option_type == "STRING":
        options = {f"{x}".title(): f"{x}" for x in setting.options}
    elif setting.option_type in {"INT", "FIXED"}:
        if isinstance(setting.options, list):
            options = {x: x for x in (f"{x}" for x in setting.options)}
        elif isinstance(setting.options, tuple):
            attributes: dict[str, str] = {
                "type": "number",
                "value": f"{default}",
            }
            extra = ""
            if len(setting.options) != 3:
                response_html = htmlgen.wrap_tag(  # type: ignore[unreachable]
                    "p",
                    "Numerical range constraints are invalid, please report!",
                    block=False,
                )
                return htmlgen.contain_in_box(response_html, box_title)
            min_, max_, step = setting.options
            attributes.update(
                {
                    "min": f"{min_}",
                    "max": f"{max_}",
                },
            )
            extra = f", Min {min_}, Max {max_}"
            if step != 0:
                attributes["step"] = f"{step}"
                if step != 1:
                    extra += f", Step {step}"
            elif setting.option_type == "FIXED":
                attributes["step"] = "any"
                extra += ", w/ decimal support"
            if setting.option_type == "INT" and min_ == -1:
                extra += " (-1 means autocalibration)"
            options = {f"Value ({setting.unit}{extra})": attributes}
    else:
        return None
    ##else:
    ##    response_html = htmlgen.wrap_tag(
    ##        "p",
    ##        f"No options exist for {setting.option_type!r} option types at this time.",
    ##        block=False,
    ##    )
    ##    return htmlgen.contain_in_box(response_html, box_title)
    ##else:
    ##    import pprint
    ##    formatted = pprint.pformat(setting)
    ##    formatted = formatted.replace(" ", "&nbsp;")
    ##    response_html = htmlgen.wrap_tag(
    ##        "textarea",
    ##        formatted,
    ##        readonly="",
    ##        rows=len(formatted.splitlines()),
    ##        cols=80,
    ##    )
    ##    return htmlgen.contain_in_box(response_html, f"{setting.title} - {setting.desc}")

    # Don't display options without settings.
    if not options:
        return None

    # If no options to select from, no need to display and waste screen space
    if len(options) == 1 and setting.option_type not in {"INT", "FIXED"}:
        return None

    if not setting.usable:
        # If not changeable, why display to user? That's asking for confusion.
        return None
        # Disable option
        ##for title, value in tuple(options.items()):
        ##    if isinstance(value, str):
        ##        options[title] = {
        ##            "value": value,
        ##            "disabled": "disabled",
        ##        }
        ##    else:
        ##        assert isinstance(options[title], dict)
        ##        options[title].update({"disabled": "disabled"})  # type: ignore[union-attr]

    return htmlgen.select_box(
        submit_name=setting.name,
        options=options,
        default=default,
        box_title=box_title,
    )


@app.get("/settings")  # type: ignore[type-var]
async def settings_get() -> AsyncIterator[str] | WerkzeugResponse:
    """Handle settings page GET."""
    scanner = request.args.get("scanner", "none")

    if scanner == "none" or scanner not in APP_STORAGE["scanners"]:
        return app.redirect("/scanners")

    device = APP_STORAGE["scanners"][scanner]
    scanner_settings = APP_STORAGE["device_settings"].get(device, [])

    return await stream_template(
        "settings_get.html.jinja",
        scanner=scanner,
        radios="\n".join(
            x
            for x in (
                get_setting_radio(setting) for setting in scanner_settings
            )
            if x
        ),
    )


@app.post("/settings")  # type: ignore[type-var]
async def settings_post() -> tuple[AsyncIterator[str], int] | WerkzeugResponse:
    """Handle settings page POST."""
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

    if data.get("settings_update_submit_button"):
        data.pop("settings_update_submit_button")

    errors: list[str] = []

    for setting_name, new_value in data.items():
        # Input validation
        if setting_name not in valid_settings:
            errors.append(f"{setting_name} not valid")
            continue
        idx = valid_settings[setting_name]
        if not scanner_settings[idx].usable:
            errors.append(f"{setting_name} not usable")
            continue
        options = scanner_settings[idx].options
        if isinstance(options, list) and str(new_value) not in set(
            map(str, options),
        ):
            errors.append(f"{setting_name}[{new_value}] invalid option)")
            continue
        if isinstance(options, tuple):
            if len(options) != 3:
                raise RuntimeError("Should be unreachable")
            try:
                as_float = float(new_value)
            except ValueError:
                errors.append(f"{setting_name}[{new_value}] invalid float")
                continue
            min_, max_, step = options
            if as_float < min_ or as_float > max_:
                errors.append(f"{setting_name}[{new_value}] out of bounds")
                continue
            if step and as_float % step != 0:
                errors.append(f"{setting_name}[{new_value}] bad step multiple")
                continue
        APP_STORAGE["device_settings"][device][idx].set = new_value

    if errors:
        errors.insert(
            0,
            "Request succeeded, but the following errors were encountered:",
        )
        return await get_exception_page(
            400,  # bad request
            "Bad Request",
            "<br>".join(errors),
            request.url,
        )

    # Return to page for that scanner
    return app.redirect(request.url)


@app.post(
    "/StableWSDiscoveryEndpoint/schemas-xmlsoap-org_ws_2005_04_discovery",
)
async def stable_ws_discovery_endpoint() -> WerkzeugResponse:
    """Handle stable_ws_discovery_endpoint POST."""
    data = await request.data
    print(f"StableWSDiscoveryEndpoint {data = }")

    args = request.args
    print(f"StableWSDiscoveryEndpoint URL {args = }")

    multi_dict = await request.form
    form_dict = multi_dict.to_dict()

    print(f"StableWSDiscoveryEndpoint POST {form_dict = }")
    return app.redirect("/")


async def serve_async(app: QuartTrio, config_obj: Config) -> None:
    """Serve app within a nursery."""
    async with trio.open_nursery(strict_exception_groups=True) as nursery:
        APP_STORAGE["nursery"] = nursery
        await nursery.start(serve, app, config_obj)
        await update_scanners_async()


def serve_scanner(
    device_name: str,
    *,
    secure_bind_port: int | None = None,
    insecure_bind_port: int | None = None,
    ip_addr: str | None = None,
    hypercorn: dict[str, object] | None = None,
) -> None:
    """Asynchronous Entry Point."""
    if secure_bind_port is None and insecure_bind_port is None:
        raise ValueError(
            "Port must be specified with `port` and or `ssl_port`!",
        )

    if not ip_addr:
        ip_addr = find_ip()

    if not hypercorn:
        hypercorn = {}

    logs_path = DATA_PATH / "logs"
    if not path.exists(logs_path):
        makedirs(logs_path)

    print(f"Logs Path: {str(logs_path)!r}\n")

    try:
        # Hypercorn config setup
        config: dict[str, object] = {
            "accesslog": "-",
            "errorlog": logs_path / time.strftime("log_%Y_%m_%d.log"),
        }
        # Load things from user controlled toml file for hypercorn
        config.update(hypercorn)
        # Override a few particularly important details if set by user
        config.update(
            {
                "worker_class": "trio",
            },
        )
        # Make sure address is in bind

        if insecure_bind_port is not None:
            raw_bound = config.get("insecure_bind", [])
            if not isinstance(raw_bound, Iterable):
                raise ValueError(
                    "main.bind must be an iterable object (set in config file)!",
                )
            bound = set(raw_bound)
            bound |= {f"{ip_addr}:{insecure_bind_port}"}
            config["insecure_bind"] = bound

            # If no secure port, use bind instead
            if secure_bind_port is None:
                config["bind"] = config["insecure_bind"]
                config["insecure_bind"] = []

            insecure_locations = combine_end(
                f"http://{addr}" for addr in sorted(bound)
            )
            print(f"Serving on {insecure_locations} insecurely")

        if secure_bind_port is not None:
            raw_bound = config.get("bind", [])
            if not isinstance(raw_bound, Iterable):
                raise ValueError(
                    "main.bind must be an iterable object (set in config file)!",
                )
            bound = set(raw_bound)
            bound |= {f"{ip_addr}:{secure_bind_port}"}
            config["bind"] = bound

            secure_locations = combine_end(
                f"http://{addr}" for addr in sorted(bound)
            )
            print(f"Serving on {secure_locations} securely")

        app.config["EXPLAIN_TEMPLATE_LOADING"] = False

        # We want pretty html, no jank
        app.jinja_options = {
            "trim_blocks": True,
            "lstrip_blocks": True,
        }

        app.add_url_rule("/<path:filename>", "static", app.send_static_file)

        config_obj = Config.from_mapping(config)

        APP_STORAGE["scanners"] = {}
        APP_STORAGE["default_device"] = device_name
        APP_STORAGE["device_settings"] = {}

        print("(CTRL + C to quit)")

        trio.run(serve_async, app, config_obj)
    except BaseExceptionGroup as exc:
        caught = False
        for ex in exc.exceptions:
            if isinstance(ex, KeyboardInterrupt):
                log(
                    "Shutting down from keyboard interrupt",
                    log_dir=str(logs_path),
                )
                caught = True
                break
        if not caught:
            raise

    # Delete temporary files if they exist
    if TEMP_PATH.exists():
        rmtree(TEMP_PATH, ignore_errors=True)


def run() -> None:
    """Run scanner server."""
    pil_version = getattr(Image, "__version__", None)
    assert pil_version is not None, "PIL should have a version!"
    print(f"PIL Image Version: {pil_version}\n")

    if not path.exists(CONFIG_PATH):
        makedirs(CONFIG_PATH)
    if not path.exists(MAIN_CONFIG):
        with open(MAIN_CONFIG, "w", encoding="utf-8") as fp:
            fp.write(
                """[main]
# Name of scanner to use on default as displayed on the webpage
# or by model as listed with `scanimage --formatted-device-list "%m%n"`
printer = "Canon PIXMA MG3600 Series"

# Port server should run on.
# You might want to consider changing this to 80
port = 3004

# Port for SSL secured server to run on
#ssl_port = 443

# Helpful stack exchange website question on how to allow non root processes
# to bind to lower numbered ports
# https://superuser.com/questions/710253/allow-non-root-process-to-bind-to-port-80-and-443
# Answer I used: https://superuser.com/a/1482188/1879931

[hypercorn]
# See https://hypercorn.readthedocs.io/en/latest/how_to_guides/configuring.html#configuration-options
use_reloader = false
# SSL configuration details
#certfile = "/home/<your_username>/letsencrypt/config/live/<your_domain_name>.duckdns.org/fullchain.pem"
#keyfile = "/home/<your_username>/letsencrypt/config/live/<your_domain_name>.duckdns.org/privkey.pem"
""",
            )

    print(f"Reading configuration file {str(MAIN_CONFIG)!r}...\n")

    with open(MAIN_CONFIG, "rb") as fp:
        config = tomllib.load(fp)

    main_section = config.get("main", {})

    target = main_section.get("printer", None)
    insecure_bind_port = main_section.get("port", None)
    secure_bind_port = main_section.get("ssl_port", None)

    hypercorn: dict[str, object] = config.get("hypercorn", {})

    print(f"Default Printer: {target}\n")

    if target == "None":
        print("No default device in config file.\n")

    ip_address: str | None = None
    if "--local" in sys.argv[1:]:
        ip_address = "127.0.0.1"

    serve_scanner(
        target,
        secure_bind_port=secure_bind_port,
        insecure_bind_port=insecure_bind_port,
        ip_addr=ip_address,
        hypercorn=hypercorn,
    )


def sane_run() -> None:
    """Run but also handle initializing and un-initializing SANE."""
    try:
        run()
    finally:
        stop_sane()


if __name__ == "__main__":
    sane_run()
