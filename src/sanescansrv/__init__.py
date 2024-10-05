"""Sane Scanner Server.

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

__license__ = "GNU General Public License Version 3"

from sanescansrv.server import (
    __author__ as __author__,
    __title__ as __title__,
    __version__ as __version__,
    sane_run as run,
)

if __name__ == "__main__":
    print(f"{__title__} v{__version__}  Copyright (C) 2022-2024  {__author__}\n")
    run()
