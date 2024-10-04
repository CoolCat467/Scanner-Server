"""Generate pages for the sane scanner web server.

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

__title__ = "Generate Pages"
__author__ = "CoolCat467"
__license__ = "GNU General Public License Version 3"


import pathlib
from typing import TYPE_CHECKING, Final

from sanescansrv import htmlgen, server

if TYPE_CHECKING:
    from collections.abc import Callable

TEMPLATE_FOLDER: Final = pathlib.Path("templates")
TEMPLATE_FUNCTIONS: dict[str, Callable[[], str]] = {}
STATIC_FOLDER: Final = pathlib.Path("static")
STATIC_FUNCTIONS: dict[str, Callable[[], str]] = {}


def save_template(name: str, content: str) -> None:
    """Save content as new template "{name}"."""
    assert TEMPLATE_FOLDER is not None
    template_path = TEMPLATE_FOLDER / f"{name}.html.jinja"
    with open(template_path, "w", encoding="utf-8") as template_file:
        template_file.write(content)
        template_file.write("\n")
    print(f"Saved content to {template_path}")


def save_static(filename: str, content: str) -> None:
    """Save content as new static file "{filename}"."""
    assert STATIC_FOLDER is not None
    static_path = STATIC_FOLDER / filename
    with open(static_path, "w", encoding="utf-8") as static_file:
        static_file.write(content)
        static_file.write("\n")
    print(f"Saved content to {static_path}")


def save_template_as(
    filename: str,
) -> Callable[[Callable[[], str]], Callable[[], str]]:
    """Save generated template as filename."""

    def function_wrapper(function: Callable[[], str]) -> Callable[[], str]:
        if filename in TEMPLATE_FUNCTIONS:
            raise NameError(
                f"{filename!r} already exists as template filename",
            )
        TEMPLATE_FUNCTIONS[filename] = function
        return function

    return function_wrapper


def save_static_as(
    filename: str,
) -> Callable[[Callable[[], str]], Callable[[], str]]:
    """Save generated static file as filename."""

    def function_wrapper(function: Callable[[], str]) -> Callable[[], str]:
        if filename in STATIC_FUNCTIONS:
            raise NameError(f"{filename!r} already exists as static filename")
        STATIC_FUNCTIONS[filename] = function
        return function

    return function_wrapper


@save_static_as("style.css")
def generate_style_css() -> str:
    """Generate style.css static file."""
    mono = "SFMono-Regular,SF Mono,Menlo,Consolas,Liberation Mono,monospace"
    return "\n".join(
        (
            htmlgen.css(
                ("*", "*::before", "*::after"),
                box_sizing="border-box",
                font_family="Lucida Console",
            ),
            htmlgen.css(("h1", "footer"), text_align="center"),
            htmlgen.css(("html", "body"), height="100%"),
            htmlgen.css(
                "body",
                line_height=1.5,
                _webkit_font_smoothing="antialiased",
                display="flex",
                flex_direction="column",
            ),
            htmlgen.css(".content", flex=(1, 0, "auto")),
            htmlgen.css(
                ".footer",
                flex_shrink=0,
            ),
            htmlgen.css(
                ("img", "picture", "video", "canvas", "svg"),
                display="block",
                max_width="100%",
            ),
            htmlgen.css(
                ("input", "button", "textarea", "select"),
                font="inherit",
            ),
            htmlgen.css(
                ("p", "h1", "h2", "h3", "h4", "h5", "h6"),
                overflow_wrap="break-word",
            ),
            htmlgen.css(
                ("#root", "#__next"),
                isolation="isolate",
            ),
            htmlgen.css(
                "code",
                padding=("0.2em", "0.4em"),
                background_color="rgba(158,167,179,0.4)",
                border_radius="6px",
                font_family=mono,
                line_height=1.5,
            ),
            htmlgen.css(
                "::placeholder",
                font_style="italic",
            ),
            htmlgen.css(
                ".box",
                background="ghostwhite",
                padding="0.5%",
                border_radius="4px",
                border=("2px", "solid", "black"),
                margin="0.5%",
                width="fit-content",
            ),
            htmlgen.css(
                "#noticeText",
                font_size="10px",
                display="inline-block",
                white_space="normal",  # change here
            ),
            htmlgen.css(
                'input[type="submit"]',
                border=("1.5px", "solid", "black"),
                border_radius="4px",
                padding="0.5rem",  # change here
                margin_left="0.5rem",
                margin_right="0.5rem",
                min_width="min-content",
            ),
        ),
    )


def template(
    title: str,
    body: str,
    *,
    head: str = "",
    body_tag: dict[str, htmlgen.TagArg] | None = None,
    lang: str = "en",
) -> str:
    """HTML Template for application."""
    head_data = "\n".join(
        (
            htmlgen.tag(
                "link",
                rel="stylesheet",
                type_="text/css",
                href="/style.css",
            ),
            head,
        ),
    )

    join_body = (
        htmlgen.wrap_tag("h1", title, False),
        body,
    )

    footer = f"{server.__title__} v{server.__version__} © {server.__author__}"

    body_data = "\n".join(
        (
            htmlgen.wrap_tag(
                "div",
                "\n".join(join_body),
                class_="content",
            ),
            htmlgen.wrap_tag(
                "footer",
                "\n".join(
                    (
                        htmlgen.wrap_tag(
                            "i",
                            "If you're reading this, the web server was installed correctly.™",
                            block=False,
                        ),
                        htmlgen.tag("hr"),
                        htmlgen.wrap_tag(
                            "p",
                            footer,
                            block=False,
                        ),
                    ),
                ),
            ),
        ),
    )

    return htmlgen.template(
        title,
        body_data,
        head=head_data,
        body_tag=body_tag,
        lang=lang,
    )


@save_template_as("error_page")
def generate_error_page() -> str:
    """Generate error response page."""
    error_text = htmlgen.wrap_tag("p", htmlgen.jinja_expression("error_body"))
    content = "\n".join(
        (
            error_text,
            htmlgen.tag("br"),
            htmlgen.jinja_if_block(
                {
                    "return_link": "\n".join(
                        (
                            htmlgen.create_link(
                                htmlgen.jinja_expression("return_link"),
                                "Return to previous page",
                            ),
                            htmlgen.tag("br"),
                        ),
                    ),
                },
            ),
            htmlgen.create_link("/", "Return to main page"),
        ),
    )
    body = htmlgen.contain_in_box(content)
    return template(
        htmlgen.jinja_expression("page_title"),
        body,
    )


@save_template_as("root_get")
def generate_root_get() -> str:
    """Generate / (root) GET page."""
    link = htmlgen.create_link("/update_scanners", "Update Devices")

    scanner_select = htmlgen.contain_in_box(
        htmlgen.jinja_radio_select(
            "scanner",
            "scanners",
            "default",
            # htmlgen.jinja_statement('default'),
            htmlgen.select_dict(
                "scanner",
                {f"None - {link}": "none"},
                "none",
            ),
        ),
        "Select a Scanner:",
    )

    image_format = htmlgen.select_box(
        "img_format",
        {v.upper(): v for v in ("png", "jpeg", "pnm", "tiff")},
        "png",
        "Select Image format:",
    )

    form_content = "\n".join((image_format, scanner_select))

    contents = htmlgen.form(
        "scan_request",
        form_content,
        "Scan!",
        "Press Scan to start scanning.",
    )

    html = "\n".join(
        (
            contents,
            htmlgen.tag("hr"),
            htmlgen.create_link(
                "/update_scanners",
                htmlgen.wrap_tag(
                    "button",
                    "Update Devices",
                    block=False,
                ),
            ),
            htmlgen.create_link(
                "/scanners",
                htmlgen.wrap_tag(
                    "button",
                    "Scanner Settings",
                    block=False,
                ),
            ),
        ),
    )

    return template("Request Scan", html)


@save_template_as("scanners_get")
def generate_scanners_get() -> str:
    """Generate /scanners GET page."""
    scanners = htmlgen.jinja_bullet_list(
        ("link", "disp"),
        "scanners.items()",
        htmlgen.create_link(
            htmlgen.jinja_expression("link"),
            htmlgen.jinja_expression("disp"),
        ),
        else_content=htmlgen.wrap_tag(
            "p",
            "There were no devices detected. Are your device(s) turned on?",
            block=False,
        ),
    )

    contents = htmlgen.contain_in_box(scanners, "Devices:")
    html = "\n".join(
        (
            contents,
            htmlgen.tag("br"),
            htmlgen.create_link(
                "/",
                htmlgen.wrap_tag("button", "Scan Request"),
            ),
            htmlgen.create_link(
                "/update_scanners",
                htmlgen.wrap_tag("button", "Update Devices"),
            ),
        ),
    )

    return template("Devices", html)


@save_template_as("settings_get")
def generate_settings_get() -> str:
    """Generate /settings GET page."""
    scanner = htmlgen.jinja_expression("scanner")
    contents = htmlgen.jinja_if_block(
        {
            "radios": htmlgen.form(
                "settings_update",
                htmlgen.jinja_expression("radios"),
                "Save",
                f'Settings for "{scanner}":',
            ),
            "": htmlgen.wrap_tag(
                "p",
                "There are no additional settings for this scanner.",
                block=False,
            ),
        },
    )
    html = "\n".join(
        (
            contents,
            htmlgen.tag("hr"),
            htmlgen.create_link(
                "/",
                htmlgen.wrap_tag(
                    "button",
                    "Scan Request",
                    block=False,
                ),
            ),
            htmlgen.create_link(
                "/scanners",
                htmlgen.wrap_tag(
                    "button",
                    "Scanner Settings",
                    block=False,
                ),
            ),
        ),
    )
    return template(scanner, html)


@save_template_as("scan-status_get")
def generate_scan_status_get() -> str:
    """Generate /scan-status GET page."""
    refreshes_after = htmlgen.jinja_expression("refreshes_after")
    estimated_wait = htmlgen.jinja_expression("estimated_wait")

    percent = htmlgen.jinja_expression("(progress[0] / progress[1] * 100)|round(2)")
    is_done = "progress[0] == progress[1]"

    title = htmlgen.jinja_if_block(
        {
            "just_started": "Just Started Scanning",
            "": "Scan Is In Progress",
        },
        block=False,
    )

    head = htmlgen.tag("meta", http_equiv="refresh", content=refreshes_after)

    percent_complete = htmlgen.wrap_tag("strong", f"{percent}%", block=False)

    estimate_strong = htmlgen.wrap_tag("strong", estimated_wait, block=False)
    ##estimate_plural = htmlgen.jinja_number_plural("estimated_wait", "second")
    ##estimate = f"{estimate_strong} {estimate_plural}"
    estimate = estimate_strong

    refresh_link = htmlgen.create_link("/scan-status", "this link")

    refresh_time_plural = htmlgen.jinja_number_plural("refreshes_after", "second")
    refresh_time_display = f"{refreshes_after} {refresh_time_plural}"

    content = "\n".join(
        (
            htmlgen.wrap_tag(
                "p",
                htmlgen.jinja_if_block(
                    {
                        "just_started": "Just Started.",
                        is_done: "Just finished, saving file...",
                        "": f"{percent_complete} Complete",
                    },
                    block=False,
                ),
                block=False,
            ),
            htmlgen.jinja_if_block(
                {
                    f"not {is_done}": htmlgen.wrap_tag(
                        "p",
                        f"Scan is estimated to be done in {estimate}.",
                        block=False,
                    ),
                },
                block=False,
            ),
        ),
    )

    body = "\n".join(
        (
            htmlgen.contain_in_box(content),
            "<hr>",
            htmlgen.wrap_tag(
                "i",
                f"This page will automatically refresh after {refresh_time_display}.",
                block=False,
            ),
            htmlgen.wrap_tag(
                "i",
                f"If it doesn't, please click {refresh_link}.",
                block=False,
            ),
        ),
    )

    return template(title, body, head=head)


def run() -> None:
    """Generate all page templates and static files."""
    for filename, function in TEMPLATE_FUNCTIONS.items():
        save_template(filename, function())
    for filename, function in STATIC_FUNCTIONS.items():
        save_static(filename, function())


if __name__ == "__main__":
    print(f"{__title__}\nProgrammed by {__author__}.\n")
    run()
