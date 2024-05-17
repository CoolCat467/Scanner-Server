"""Elapsed Time."""

# Programmed by CoolCat467

from __future__ import annotations

# Elapsed Time
# Copyright (C) 2022-2024  CoolCat467
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__title__ = "Elapsed Time"
__author__ = "CoolCat467"
__license__ = "GNU General Public License Version 3"


def split_time(seconds: int) -> list[int]:
    """Split time into units of time."""
    seconds = int(seconds)

    # values = (1, 60, 60, 24, 7, 365/12/7, 12, 10, 10, 10, 1000, 10, 10, 5)
    # mults = {0:values[0]}
    # for i in range(len(values)):
    #     mults[i+1] = round(mults[i] * values[i])
    # divs = list(reversed(mults.values()))[:-1]
    divs = (
        15768000000000000,
        3153600000000000,
        315360000000000,
        31536000000000,
        31536000000,
        3153600000,
        315360000,
        31536000,
        2628000,
        604800,
        86400,
        3600,
        60,
        1,
    )
    ret = []
    for num in divs:
        divisions, seconds = divmod(seconds, num)
        ret.append(divisions)
    return ret


def combine_end(data: Iterable[str], final: str = "and") -> str:
    """Join values of text, and have final with the last one properly."""
    data = list(map(str, data))
    if len(data) >= 2:
        data[-1] = f"{final} {data[-1]}"
    if len(data) > 2:
        return ", ".join(data)
    return " ".join(data)


def get_elapsed(seconds: int) -> str:
    """Return elapsed time as a string."""
    times = (
        "eons",
        "eras",
        "epochs",
        "ages",
        "millennia",
        "centuries",
        "decades",
        "years",
        "months",
        "weeks",
        "days",
        "hours",
        "minutes",
        "seconds",
    )
    single = [i[:-1] for i in times]
    single[4] = "millennium"
    single[5] = "century"

    negative_flag = seconds < 0
    if negative_flag:
        seconds = -seconds

    split = split_time(seconds)
    zip_index = [(i, v) for i, v in enumerate(split) if v]

    data = []
    for index, value in zip_index:
        title = single[index] if value < 2 else times[index]
        data.append(f"{value} {title}")

    if negative_flag:
        data[0] = "Negative " + data[0]
    return combine_end(data)


def split_end(data: str, final: str = "and") -> list[str]:
    """Split a combine_end joined string."""
    values = data.split(", ")
    values.extend(values.pop().split(final, 1))
    return [v.strip() for v in values if v]


def get_time_of_day(hour: int, season: int = 0) -> str:
    """Figure out and return what time of day it is.

    If season is -1, it is winter and afternoon is 12 PM to 4 PM
    If season is  0, season is unknown and afternoon is 12 PM to 6 PM
    If season is  1, it is summer and afternoon is 12 PM to 8 PM
    """
    season_offset = season << 1  # quick multiply by 2

    if hour > 4 and hour < 12:
        return "Morning"
    if hour > 11 and hour < (19 + season_offset):
        # "Afternoon is usually from 12 PM to 6 PM,
        # but during winter it may be from 12 PM to 4 PM
        # and during summer it may be from 12 PM to 8 PM."
        return "Afternoon"
    if hour > (18 + season_offset) and hour < 22:
        return "Evening"
    return "Night"
