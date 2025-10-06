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
__version__ = "3.2.0"
__license__ = "GNU General Public License Version 3"


import functools
import itertools
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
from dataclasses import dataclass, field
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


@dataclass
class DeviceOptionsGroup:
    """A group of scanner options."""

    name: None | str
    title: None | str


if TYPE_CHECKING:

    class TypeEnum(IntEnum):
        """Type Enum."""

        TYPE_BOOL = 0
        TYPE_INT = auto()
        TYPE_FIXED = auto()
        TYPE_STRING = auto()
        TYPE_BUTTON = auto()
        TYPE_GROUP = auto()

    class UnitEnum(IntEnum):
        """Unit Enum."""

        UNIT_NONE = 0
        UNIT_PIXEL = auto()
        UNIT_BIT = auto()
        UNIT_MM = auto()
        UNIT_DPI = auto()
        UNIT_PERCENT = auto()
        UNIT_MICROSECOND = auto()

else:
    TypeEnum = IntEnum(
        "TypeEnum",
        {name: index for index, name in sane.TYPE_STR.items()},
    )
    UnitEnum = IntEnum(
        "UnitEnum",
        {name: index for index, name in sane.UNIT_STR.items()},
    )


TYPE_CONVERSION: Final = {
    TypeEnum.TYPE_BOOL: bool,
    TypeEnum.TYPE_INT: int,
    TypeEnum.TYPE_FIXED: float,
    TypeEnum.TYPE_STRING: str,
}


UNIT_CONVERSION: Final = {
    UnitEnum.UNIT_NONE: "",
    UnitEnum.UNIT_PIXEL: "px",
    UnitEnum.UNIT_BIT: "bit",
    UnitEnum.UNIT_MM: "mm",
    UnitEnum.UNIT_DPI: "dpi",
    UnitEnum.UNIT_PERCENT: "%",
    UnitEnum.UNIT_MICROSECOND: "ms",
}


def convert_type(
    sane_type: TypeEnum,
    value: str | int | float | bool | None,
) -> str | int | float | bool | None:
    """Convert the sane attribute value according to its type representation."""
    if value is None:
        return None

    convert: type[str | int | float | bool] = TYPE_CONVERSION[sane_type]
    return convert(value)


@dataclass
class DeviceOptionDataClass:
    """Data Storage for DeviceOption."""

    name: str
    title: str
    desc: str = field(repr=False)
    group: DeviceOptionsGroup
    type: TypeEnum
    unit: UnitEnum
    constraint: (
        list[str | int | bool]
        | tuple[int | float, int | float, int | float]
        | None
    )
    py_name: str = field(repr=False)
    active: bool
    settable: bool
    default: None | str | int | float | bool = field(
        init=False,
        default=None,
    )
    value: None | str | int | float = field(
        init=False,
        default=None,
    )

    def __post_init__(self) -> None:
        """Assure default types in dataclass."""
        if self.type == TypeEnum.TYPE_BOOL:
            self.constraint = [True, False]
        self.active = bool(self.active)
        self.settable = bool(self.settable)


class DeviceOption(DeviceOptionDataClass):
    """A scanner option."""

    _default: str | int | float | bool | None = None
    _value: str | int | float | bool | None = None

    @property
    def default(
        self,
    ) -> str | int | float | bool | None:
        """Default value of this option."""
        return self._default

    @default.setter
    def default(self, value: str | int | float | bool | None) -> None:
        self._default = convert_type(self.type, value)

    @property
    def value(
        self,
    ) -> str | int | float | bool | None:
        """Current value of this option."""
        if self._value is None:
            return self.default
        return self._value

    @value.setter  # type: ignore[override]
    def value(self, value: str | int | float | bool) -> None:
        if not self.settable:
            raise ValueError(f"Attribute {self.name} is not settable")
        self._value = convert_type(self.type, value)


def get_device_settings(device_addr: str) -> list[DeviceOption]:
    """Get Options for Scanner Device."""
    with sane.open(device_addr) as sane_device:
        options: list[DeviceOption] = []
        group = DeviceOptionsGroup(None, None)
        for args in sane_device.get_options():
            sane_option = sane.Option(args, sane_device)
            type_ = TypeEnum[sane.TYPE_STR[sane_option.type]]
            if type_ == TypeEnum.TYPE_GROUP:
                group = DeviceOptionsGroup(
                    sane_option.name,
                    sane_option.title,
                )
                continue
            option = DeviceOption(
                sane_option.name,
                sane_option.title,
                sane_option.desc,
                group,
                type_,
                UnitEnum[sane.UNIT_STR[sane_option.unit]],
                sane_option.constraint,
                sane_option.py_name,
                bool(sane_option.is_active()),
                sane_option.is_settable(),
            )
            if option.active:
                try:
                    option.default = getattr(sane_device, option.py_name)
                except AttributeError:
                    option.active = False
            options.append(option)
        return options


@dataclass
class Device:
    """A scanner (SANE mapping) Device."""

    """The device name, suitable for passing to sane.open()"""
    device_name: str
    """The device vendor."""
    vendor: str
    """The device model vendor."""
    model: str
    """The device type, such as "virtual device" or "video camera"."""
    type_: str
    options: list[DeviceOption] = field(
        init=False,
        default_factory=list,
        repr=False,
    )
    active: bool = field(init=False, default=True)

    @property
    def url(self) -> str:
        """Return a urlencoded name of the device."""
        return urlencode({"scanner": self.device_name})


def get_devices() -> list[Device]:
    """Return dict of SANE name to device."""
    restart_sane()
    devices: list[Device] = []
    for device in itertools.starmap(Device, sane.get_devices(localOnly=True)):
        try:
            device.options = get_device_settings(device.device_name)
        except SaneError as err:
            device.active = False
            print(f"Device {device.device_name} had an error: {err}")
        finally:
            devices.append(device)
    return devices


app: Final = QuartTrio(  # pylint: disable=invalid-name
    __name__,
    static_folder="static",
    template_folder="templates",
)
APP_STORAGE: Final[dict[str, Any]] = {}


def display_progress(current: int, total: int) -> None:
    """Display progress of the active scan."""
    print(f"{current / total * 100:.2f}%")


def preform_scan(
    device: Device,
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

    with sane.open(device.device_name) as sane_device:
        for option in device.options:
            if not option.settable:
                continue
            if option.value is None:
                continue  # cannot set None
            if option.default == option.value:
                continue  # nothing to set
            try:
                setattr(sane_device, option.py_name, option.value)
            except (AttributeError, TypeError):
                print(f"{option.name} = {option.value!r}")
        with sane_device.scan(progress) as image:
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
    device: Device,
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
                device,
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


@app.get("/scan/<scan_filename>")
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


@app.get("/scan-status")
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
    delay = 2
    estimated_wait: int = 120

    if status == ScanStatus.STARTED:
        delay = 15

    if status == ScanStatus.IN_PROGRESS:
        progress, time_deltas_ns = data

        assert isinstance(progress, ScanProgress)
        assert isinstance(time_deltas_ns, list)

        # Remove first couple of values (takes time to start)
        if len(time_deltas_ns) > 1:
            time_deltas_ns = time_deltas_ns[1:]

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


def get_default_device() -> str:
    """Retrieve the default scan device."""
    device = get_scanner_by_vendor_model(APP_STORAGE["default_device"])
    if device is not None:
        return device.device_name
    try:
        device = APP_STORAGE["scanners"][0]
        if not isinstance(device, Device):
            return "None"
        return device.device_name
    except IndexError:
        return "None"


@app.get("/")
async def root_get() -> AsyncIterator[str]:
    """Handle main page GET request."""
    return await stream_template(
        "root_get.html.jinja",
        scanners=APP_STORAGE.get("scanners", []),
        default=get_default_device(),
    )


@app.post("/")
@pretty_exception
async def root_post() -> (
    WerkzeugResponse | AsyncIterator[str] | tuple[AsyncIterator[str], int]
):
    """Handle page POST."""
    multi_dict = await request.form
    data = multi_dict.to_dict()

    # Validate input
    img_format = data.get("img_format", "png")
    if img_format not in {"pnm", "tiff", "png", "jpeg"}:
        return app.redirect("/")

    if (device := get_scanner(data.get("scanner", "none"))) is None:
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


async def update_scanners_async() -> bool:
    """Update scanners list asynchronously. Return if successful."""
    if SCAN_LOCK.locked():
        return False
    async with SCAN_LOCK:
        await trio.to_thread.run_sync(update_scanners)
    return True


@app.get("/update_scanners")
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


@app.get("/scanners")
async def scanners_get() -> AsyncIterator[str]:
    """Scanners page get handling."""
    return await stream_template(
        "scanners_get.html.jinja",
        scanners=APP_STORAGE.get("scanners", []),
    )


def get_setting_radio(option: DeviceOption) -> str | None:
    """Return setting radio section."""
    box_title = f"{option.title} - {option.desc}"

    default = option.value
    inputs: Mapping[str, str | bool | dict[str, str]] = {}
    pass_default: str | bool | None = (
        str(default) if default is not None else None
    )

    if option.type == TypeEnum.TYPE_BOOL:
        inputs = {option.name.title(): True}
        pass_default = bool(default)
    elif option.type == TypeEnum.TYPE_STRING and option.constraint is not None:
        inputs = {f"{x}".title(): f"{x}" for x in option.constraint}
    elif option.type in {TypeEnum.TYPE_INT, TypeEnum.TYPE_FIXED}:
        if isinstance(option.constraint, list):
            inputs = {x: x for x in (f"{x}" for x in option.constraint)}
        elif isinstance(option.constraint, tuple):
            attributes: dict[str, str] = {
                "type": "number",
                "value": f"{default}",
            }
            extra = ""
            if len(option.constraint) != 3:
                response_html = htmlgen.wrap_tag(  # type: ignore[unreachable]
                    "p",
                    "Numerical range constraints are invalid, please report!",
                    block=False,
                )
                return htmlgen.contain_in_box(response_html, box_title)
            min_, max_, step = option.constraint
            attributes.update(
                {
                    "min": f"{min_}",
                    "max": f"{max_}",
                },
            )
            extra = f"Min {min_}, Max {max_}"
            if step != 0:
                attributes["step"] = f"{step}"
                if step != 1:
                    extra += f", Step {step}"
            elif option.type == TypeEnum.TYPE_FIXED:
                attributes["step"] = "any"
                extra += ", w/ decimal support"
            if option.type == TypeEnum.TYPE_FIXED and min_ == -1:
                extra += " (-1 means autocalibration)"
            unit = UNIT_CONVERSION[option.unit]
            unit_data = f" [{unit}]" if unit else ""
            inputs = {f"Value ({extra}{unit_data})": attributes}
    else:
        return None
    ##else:
    ##    response_html = htmlgen.wrap_tag(
    ##        "p",
    ##        f"No option exist for {option.type!r} option types at this time.",
    ##        block=False,
    ##    )
    ##    return htmlgen.contain_in_box(response_html, box_title)
    ##else:
    ##    import pprint
    ##    formatted = pprint.pformat(option)
    ##    formatted = formatted.replace(" ", "&nbsp;")
    ##    response_html = htmlgen.wrap_tag(
    ##        "textarea",
    ##        formatted,
    ##        readonly="",
    ##        rows=len(formatted.splitlines()),
    ##        cols=80,
    ##    )
    ##    return htmlgen.contain_in_box(response_html, f"{option.title} - {option.desc}")

    # Don't display option without settings.
    if not inputs:
        return None

    # If no inputs to select from, no need to display and waste screen space
    if len(inputs) == 1 and option.type not in {
        TypeEnum.TYPE_INT,
        TypeEnum.TYPE_FIXED,
        TypeEnum.TYPE_BOOL,
    }:
        return None

    if not option.settable or not option.active:
        # If not changeable, why display to user? That's asking for confusion.
        # Note: option.active settings are readable
        return None
        # Disable option
        ##for title, value in tuple(inputs.items()):
        ##    if isinstance(value, str):
        ##        inputs[title] = {
        ##            "value": value,
        ##            "disabled": "disabled",
        ##        }
        ##    else:
        ##        assert isinstance(inputs[title], dict)
        ##        inputs[title].update({"disabled": "disabled"})  # type: ignore[union-attr]

    return htmlgen.select_box(
        submit_name=option.name,
        options=inputs,
        default=pass_default,
        box_title=box_title,
    )


def get_scanner(scanner: str) -> Device | None:
    """Get scanner device from globally stored devices."""
    devices: list[Device] = APP_STORAGE.get("scanners", [])
    scanners = [device.device_name for device in devices]
    if scanner not in scanners:
        return None
    idx = scanners.index(scanner)
    item = devices[idx]
    assert isinstance(item, Device)
    return item


def get_scanner_by_vendor_model(vendor_model: str) -> Device | None:
    """Get scanner device from globally stored devices."""
    devices: list[Device] = APP_STORAGE.get("scanners", [])
    models = [f"{device.vendor} {device.model}" for device in devices]
    if vendor_model not in models:
        return None
    idx = models.index(vendor_model)
    item = devices[idx]
    assert isinstance(item, Device)
    return item


@app.get("/settings")
async def settings_get() -> AsyncIterator[str] | WerkzeugResponse:
    """Handle settings page GET."""
    scanner = request.args.get("scanner", "none")

    if (device := get_scanner(scanner)) is None:
        return app.redirect("/scanners")

    return await stream_template(
        "settings_get.html.jinja",
        scanner=device.model,
        radios="\n".join(filter(None, map(get_setting_radio, device.options))),
    )


@app.post("/settings")
async def settings_post() -> tuple[AsyncIterator[str], int] | WerkzeugResponse:
    """Handle settings page POST."""
    scanner = request.args.get("scanner", "none")

    if (device := get_scanner(scanner)) is None:
        return app.redirect("/scanners")

    valid_settings = {
        option.name: idx for idx, option in enumerate(device.options)
    }

    multi_dict = await request.form
    data = multi_dict.to_dict()

    if data.get("settings_update_submit_button"):
        data.pop("settings_update_submit_button")

    # Web browsers don't send unchecked items
    for option in device.options:
        if (
            option.type == TypeEnum.TYPE_BOOL
            and option.settable
            and option.active
            and option.value
            and option.name not in data
        ):
            data[option.name] = "false"

    errors: list[str] = []

    for setting_name, new_value in data.items():
        # Input validation
        if setting_name not in valid_settings:
            errors.append(f"{setting_name} not valid")
            continue
        idx = valid_settings[setting_name]
        option = device.options[idx]
        if not option.settable:
            errors.append(f"{setting_name} not settable")
            continue
        if option.type == TypeEnum.TYPE_BOOL:
            new_value = new_value.title()
        constraint = option.constraint
        if isinstance(constraint, list):
            valid_options = list(map(str, constraint))
            try:
                new_value = constraint[valid_options.index(str(new_value))]
            except ValueError as err:
                errors.append(
                    f"{setting_name}[{new_value}] invalid option: {err} {valid_options}",
                )
                continue
        if isinstance(constraint, tuple):
            if len(constraint) != 3:
                raise RuntimeError("Should be unreachable")
            try:
                as_float = float(new_value)
            except ValueError:
                errors.append(f"{setting_name}[{new_value}] invalid float")
                continue
            min_, max_, step = constraint
            if as_float < min_ or as_float > max_:
                errors.append(f"{setting_name}[{new_value}] out of bounds")
                continue
            if step and as_float % step != 0:
                errors.append(f"{setting_name}[{new_value}] bad step multiple")
                continue
        option.value = new_value

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
                f"https://{addr}" for addr in sorted(bound)
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

        APP_STORAGE["scanners"] = []
        APP_STORAGE["default_device"] = device_name

        print("(CTRL + C to quit)")

        trio.run(serve_async, app, config_obj)
    except BaseExceptionGroup as exc:
        caught = False
        for ex in exc.exceptions:
            if isinstance(ex, KeyboardInterrupt):
                print("Shutting down from keyboard interrupt")
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
printer = "None"

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
