import re
from difflib import ndiff


def escape_control_characters(input_str: str) -> str:
    """
    Escape non-visible characters in a string except for \f, \n, \r, \t, and \v.
    """
    sub_str = re.sub(r"(\\x[0-9a-fA-F]{2})", r"\\\\\1", input_str)
    return "".join(
        (
            f"\\x{ord(char):02x}"
            if (
                ord(char) < 0x20
                or ord(char) == 0x7F
                or (ord(char) > 0x7E and ord(char) < 0xA0)
            )
            and char not in "\f\n\r\t\v"
            else char
        )
        for char in sub_str
    )


def encode_control_characters(input_str: str) -> str:
    """
    Convert hexadecimal escape sequences in a string to their actual control characters
    unless the sequence is escaped (i.e., preceded by an additional backslash).
    """
    # Pattern to match an unescaped hexadecimal sequence
    pattern = re.compile(r"(?<!\\)(?:\\\\)*\\x([0-9a-fA-F]{2})")

    # Function to replace matched patterns with the corresponding character
    def replace_hex(match):
        # Get the hex part and convert it to the corresponding ASCII character
        hex_value = match.group(1)
        return chr(int(hex_value, 16))

    # Replace all occurrences of the pattern in the input string
    result = pattern.sub(replace_hex, input_str)
    return result


def diff_str(expected: str, actual: str) -> str:
    if expected == actual:
        return None
    escaped_expected = escape_control_characters(expected)
    escaped_actual = escape_control_characters(actual)
    diff = "".join(
        "\n".join(
            ndiff(expected.splitlines(keepends=True), actual.splitlines(keepends=True))
        )
    )
    return (
        f"Expected:\n{escaped_expected}\n\nActual:\n{escaped_actual}\n\nDiff:\n{diff}"
    )
