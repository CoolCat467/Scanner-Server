"""Simple logging to both console and log files.

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

__title__ = "Logging"
__author__ = "CoolCat467"
__license__ = "GNU General Public License Version 3"


import time
from os import makedirs, path

PROGRAM_TITLE: str = __title__


def set_title(title: str) -> None:
    """Set program title."""
    global PROGRAM_TITLE
    PROGRAM_TITLE = title


def log(message: str, level: int = 1, log_dir: str | None = None) -> None:
    """Log a message to console and log file."""
    levels = ["DEBUG", "INFO", "ERROR"]

    if log_dir is None:
        # log_dir = path.join(path.dirname(__file__), "logs")
        log_dir = path.abspath(
            path.expanduser(path.join("~", ".sanescansrv", "logs")),
        )
    if not path.exists(log_dir):
        makedirs(log_dir, exist_ok=True)
    filename = time.strftime("log_%Y_%m_%d.log")
    log_file = path.join(log_dir, filename)

    log_level = levels[min(max(0, level), len(levels) - 1)]
    log_time = time.asctime()
    log_message_text = message.encode("unicode_escape").decode("utf-8")

    log_msg = f"[{PROGRAM_TITLE}] [{log_time}] [{log_level}] {log_message_text}"

    # Open in append mode; this will create the file if it doesn't exist
    with open(log_file, mode="a", encoding="utf-8") as file:
        file.write(f"{log_msg}\n")  # This handles both file creation and writing
    print(log_msg)


if __name__ == "__main__":
    print(f"{__title__}\nProgrammed by {__author__}.\n")
