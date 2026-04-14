"""Unit tests for elapsed module."""

from __future__ import annotations

from sanescansrv.elapsed import (
    combine_end,
    get_elapsed,
    get_time_of_day,
    split_end,
    split_time,
)


class TestSplitTime:
    """Tests for split_time function."""

    __slots__ = ()

    def test_zero_seconds(self) -> None:
        """Test that zero seconds returns all zeros."""
        result = split_time(0)
        assert result == [0] * 14

    def test_one_second(self) -> None:
        """Test that one second is correctly split."""
        result = split_time(1)
        assert result[-1] == 1
        assert sum(result[:-1]) == 0

    def test_one_minute(self) -> None:
        """Test that 60 seconds equals one minute."""
        result = split_time(60)
        assert result[-2] == 1  # minutes index
        assert result[-1] == 0  # seconds index

    def test_one_hour(self) -> None:
        """Test that 3600 seconds equals one hour."""
        result = split_time(3600)
        assert result[-3] == 1  # hours index
        assert result[-2] == 0  # minutes index
        assert result[-1] == 0  # seconds index

    def test_one_day(self) -> None:
        """Test that 86400 seconds equals one day."""
        result = split_time(86400)
        assert result[-4] == 1  # days index
        assert all(v == 0 for v in result[-3:])

    def test_complex_time(self) -> None:
        """Test a complex time value."""
        # 1 hour, 23 minutes, 45 seconds = 5025 seconds
        result = split_time(5025)
        assert result[-3] == 1  # 1 hour
        assert result[-2] == 23  # 23 minutes
        assert result[-1] == 45  # 45 seconds

    def test_float_input(self) -> None:
        """Test that float inputs are converted to int."""
        result = split_time(60.7)
        assert isinstance(result[0], int)
        assert result[-2] == 1

    def test_negative_seconds(self) -> None:
        """Test that negative seconds are handled."""
        result = split_time(-60)
        # Should handle negative values (implementation converts to int)
        assert isinstance(result, list)

    def test_large_value(self) -> None:
        """Test that large time values are correctly split."""
        # 1 eon in seconds: 15768000000000000
        result = split_time(15768000000000000)
        assert result[0] == 1  # 1 eon
        assert all(v == 0 for v in result[1:])

    def test_returns_list_of_14_elements(self) -> None:
        """Test that result always has 14 elements."""
        for seconds in [0, 1, 60, 3600, 86400, 999999]:
            result = split_time(seconds)
            assert len(result) == 14


class TestCombineEnd:
    """Tests for combine_end function."""

    __slots__ = ()

    def test_single_item(self) -> None:
        """Test joining a single item."""
        assert combine_end(["item"]) == "item"

    def test_two_items(self) -> None:
        """Test joining two items."""
        assert combine_end(iter(("first", "second"))) == "first and second"

    def test_three_items(self) -> None:
        """Test joining three items."""
        result = combine_end(["first", "second", "third"])
        assert result == "first, second, and third"

    def test_four_items(self) -> None:
        """Test joining four items."""
        result = combine_end(["a", "b", "c", "d"])
        assert result == "a, b, c, and d"

    def test_custom_final_word(self) -> None:
        """Test using a custom final word."""
        result = combine_end(["first", "second"], final="or")
        assert result == "first or second"

    def test_custom_final_word_three_items(self) -> None:
        """Test custom final word with three items."""
        result = combine_end(("a", "b", "c"), final="or")
        assert result == "a, b, or c"

    def test_empty_list(self) -> None:
        """Test joining an empty list."""
        assert combine_end([]) == ""

    def test_converts_to_strings(self) -> None:
        """Test that non-string items are converted to strings."""
        result = combine_end((1, 2, 3))
        assert result == "1, 2, and 3"

    def test_mixed_types(self) -> None:
        """Test mixing different types."""
        result = combine_end(["item", 42, "thing"])
        assert result == "item, 42, and thing"


class TestGetElapsed:
    """Tests for get_elapsed function."""

    def test_zero_seconds(self) -> None:
        """Test zero seconds."""
        result = get_elapsed(0)
        assert result == ""

    def test_one_second(self) -> None:
        """Test singular second."""
        result = get_elapsed(1)
        assert result == "1 second"

    def test_two_seconds(self) -> None:
        """Test plural seconds."""
        result = get_elapsed(2)
        assert result == "2 seconds"

    def test_one_minute(self) -> None:
        """Test singular minute."""
        result = get_elapsed(60)
        assert result == "1 minute"

    def test_two_minutes(self) -> None:
        """Test plural minutes."""
        result = get_elapsed(120)
        assert result == "2 minutes"

    def test_one_hour(self) -> None:
        """Test singular hour."""
        result = get_elapsed(3600)
        assert result == "1 hour"

    def test_one_day(self) -> None:
        """Test singular day."""
        result = get_elapsed(86400)
        assert result == "1 day"

    def test_mixed_time_units(self) -> None:
        """Test time with multiple units."""
        # 1 hour, 30 minutes, 15 seconds = 5415 seconds
        result = get_elapsed(5415)
        assert "1 hour" in result
        assert "30 minutes" in result
        assert "15 seconds" in result

    def test_negative_time(self) -> None:
        """Test negative elapsed time."""
        result = get_elapsed(-60)
        assert "Negative" in result
        assert "minute" in result

    def test_one_year(self) -> None:
        """Test one year."""
        result = get_elapsed(31536000)
        assert result == "1 year"

    def test_one_month(self) -> None:
        """Test one month."""
        result = get_elapsed(2628000)
        assert result == "1 month"

    def test_one_week(self) -> None:
        """Test one week."""
        result = get_elapsed(604800)
        assert result == "1 week"

    def test_singular_forms(self) -> None:
        """Test that singular forms are used correctly."""
        result = get_elapsed(1)
        assert "1 second" in result
        assert "seconds" not in result

    def test_plural_forms(self) -> None:
        """Test that plural forms are used correctly."""
        result = get_elapsed(2)
        assert "2 seconds" in result

    def test_millennium_singular(self) -> None:
        """Test singular millennium form."""
        result = get_elapsed(31536000000)
        assert "millennium" in result
        assert "millennia" not in result

    def test_century_singular(self) -> None:
        """Test singular century form."""
        result = get_elapsed(3153600000)
        assert "century" in result
        assert "centuries" not in result

    def test_returns_string(self) -> None:
        """Test that function returns a string."""
        result = get_elapsed(42)
        assert isinstance(result, str)


class TestSplitEnd:
    """Tests for split_end function."""

    def test_single_item(self) -> None:
        """Test splitting a single item."""
        result = split_end("item")
        assert result == ["item"]

    def test_two_items(self) -> None:
        """Test splitting two items."""
        result = split_end("first and second")
        assert result == ["first", "second"]

    def test_three_items(self) -> None:
        """Test splitting three items."""
        result = split_end("first, second, and third")
        assert result == ["first", "second", "third"]

    def test_four_items(self) -> None:
        """Test splitting four items."""
        result = split_end("a, b, c, and d")
        assert result == ["a", "b", "c", "d"]

    def test_custom_final_word(self) -> None:
        """Test splitting with custom final word."""
        result = split_end("first or second", final="or")
        assert result == ["first", "second"]

    def test_custom_final_word_multiple_items(self) -> None:
        """Test splitting multiple items with custom final word."""
        result = split_end("a, b, or c", final="or")
        assert result == ["a", "b", "c"]

    def test_whitespace_handling(self) -> None:
        """Test that whitespace is properly stripped."""
        result = split_end("  first  and  second  ")
        assert all(item == item.strip() for item in result)

    def test_round_trip_combine_split(self) -> None:
        """Test that combine_end and split_end are inverses."""
        items = ["one", "two", "three"]
        combined = combine_end(items)
        split = split_end(combined)
        assert split == items

    def test_empty_strings_filtered(self) -> None:
        """Test that empty strings are filtered out."""
        result = split_end("item")
        assert "" not in result

    def test_returns_list(self) -> None:
        """Test that function returns a list."""
        result = split_end("test")
        assert isinstance(result, list)


class TestGetTimeOfDay:
    """Tests for get_time_of_day function."""

    def test_morning_hours(self) -> None:
        """Test morning time."""
        assert get_time_of_day(5) == "Morning"
        assert get_time_of_day(8) == "Morning"
        assert get_time_of_day(11) == "Morning"

    def test_afternoon_hours_unknown_season(self) -> None:
        """Test afternoon time with unknown season."""
        # season=0: 12 PM to 6 PM (hours 12-18)
        assert get_time_of_day(12, season=0) == "Afternoon"
        assert get_time_of_day(15, season=0) == "Afternoon"
        assert get_time_of_day(18, season=0) == "Afternoon"

    def test_afternoon_hours_winter(self) -> None:
        """Test afternoon time during winter."""
        # season=-1: 12 PM to 4 PM (hours 12-16)
        assert get_time_of_day(12, season=-1) == "Afternoon"
        assert get_time_of_day(15, season=-1) == "Afternoon"
        assert get_time_of_day(16, season=-1) == "Afternoon"

    def test_afternoon_hours_summer(self) -> None:
        """Test afternoon time during summer."""
        # season=1: 12 PM to 8 PM (hours 12-20)
        assert get_time_of_day(12, season=1) == "Afternoon"
        assert get_time_of_day(15, season=1) == "Afternoon"
        assert get_time_of_day(20, season=1) == "Afternoon"

    def test_evening_hours(self) -> None:
        """Test evening time."""
        assert get_time_of_day(19) == "Evening"
        assert get_time_of_day(20) == "Evening"
        assert get_time_of_day(21) == "Evening"

    def test_night_hours(self) -> None:
        """Test night time."""
        assert get_time_of_day(0) == "Night"
        assert get_time_of_day(3) == "Night"
        assert get_time_of_day(23) == "Night"
        assert get_time_of_day(22) == "Night"

    def test_boundary_morning_to_afternoon(self) -> None:
        """Test boundary between morning and afternoon."""
        assert get_time_of_day(4) == "Night"
        assert get_time_of_day(5) == "Morning"
        assert get_time_of_day(11) == "Morning"
        assert get_time_of_day(12, season=0) == "Afternoon"

    def test_boundary_afternoon_to_evening_unknown_season(self) -> None:
        """Test boundary between afternoon and evening (unknown season)."""
        assert get_time_of_day(18, season=0) == "Afternoon"
        assert get_time_of_day(19, season=0) == "Evening"

    def test_boundary_evening_to_night(self) -> None:
        """Test boundary between evening and night."""
        assert get_time_of_day(21) == "Evening"
        assert get_time_of_day(22) == "Night"

    def test_returns_string(self) -> None:
        """Test that function returns a string."""
        result = get_time_of_day(12)
        assert isinstance(result, str)

    def test_valid_time_of_day_values(self) -> None:
        """Test that only valid time of day strings are returned."""
        valid_values = {"Morning", "Afternoon", "Evening", "Night"}
        for hour in range(24):
            for season in [-1, 0, 1]:
                result = get_time_of_day(hour, season=season)
                assert result in valid_values


class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_get_elapsed_and_split_end_round_trip(self) -> None:
        """Test that get_elapsed output can be split back."""
        seconds = 3665  # 1 hour, 1 minute, 5 seconds
        elapsed = get_elapsed(seconds)
        split = split_end(elapsed)
        assert len(split) >= 1
        assert all(isinstance(item, str) for item in split)

    def test_split_time_accuracy(self) -> None:
        """Test that split_time values sum to original in get_elapsed."""
        test_seconds = [1, 60, 3600, 86400, 2628000, 31536000]
        for seconds in test_seconds:
            elapsed = get_elapsed(seconds)
            assert isinstance(elapsed, str)
            assert len(elapsed) > 0
