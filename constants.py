import pathlib
from collections.abc import Callable
from typing import Final



class Page_Constants:
    TEMPLATE_FOLDER: Final = pathlib.Path("templates")
    TEMPLATE_FUNCTIONS: dict[str, Callable[[], str]] = {}
    STATIC_FOLDER: Final = pathlib.Path("static")
    STATIC_FUNCTIONS: dict[str, Callable[[], str]] = {}

class Value_Error_Constant:
    WRAP_ERROR = "Attempted comment escape"
    CONDITION_ERROR = "Found condition after else block defined"
    COUNT_ERROR = "There must be at least one condition for there to be an "
    "else block"
    TITLE_ERROR = "Title must not contain spaces and must not be blank"
    SCAN_ERROR = "Output type must be pnm, tiff, png, or jpeg"
    TARGET_ERROR = "No default device in config file."
    KEYBOARD_ERROR = "Shutting down from keyboard interrupt"
    COMMENT_ERROR = "Attempted comment escape"
    FIELD_ERROR = "Attribute 'id' conflicts with an internal attribute"
    IF_BLOCK_ERROR = "Found condition after else block defined"
    NO_IF_BLOCK_ERROR = "There must be at least one condition for there to be an else block"
    INVALID_TITLE_ERROR = "Title must not contain spaces and must not be blank"

class Wrap_Constants:
    COMMENT = "This is comment"
    COMMENT_RESULT =  """<!--
                    this is comment
                    -->"""
    INLINE_COMMENT = "smol comment"
    INLINE_RESULT = "<!--smol comment-->"
    WRAP_COMMENT = "-->haha now javascript hacks you"
    


