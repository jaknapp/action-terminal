from action.app.str_utils import encode_control_characters, escape_control_characters
from action.app.test_utils import assert_str_equal


def test_escape_control_characters():
    test_input = bytes(range(256)).decode("latin-1")
    assert_str_equal(
        "\\x00\\x01\\x02\\x03\\x04\\x05\\x06\\x07\\x08\t\n\x0b\x0c\r\\x0e\\x0f\\x10"
        "\\x11\\x12\\x13\\x14\\x15\\x16\\x17\\x18\\x19\\x1a\\x1b\\x1c\\x1d\\x1e\\x1f "
        "!\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijk"
        "lmnopqrstuvwxyz{|}~\\x7f\\x80\\x81\\x82\\x83\\x84\\x85\\x86\\x87\\x88\\x89"
        "\\x8a\\x8b\\x8c\\x8d\\x8e\\x8f\\x90\\x91\\x92\\x93\\x94\\x95\\x96\\x97\\x98"
        "\\x99\\x9a\\x9b\\x9c\\x9d\\x9e\\x9f\xa0¡¢£¤¥¦§¨©ª«¬\xad®¯°±²³´µ¶·¸¹º»¼½¾¿ÀÁÂÃ"
        "ÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ",
        escape_control_characters(test_input),
    )


def test_escape_hex_strings():
    assert_str_equal(
        "\\x00\\x19 }~\\x7f\\x9f\xa0ÿ\f\n\r\t\v",
        escape_control_characters("\x00\x19\x20\x7d\x7e\x7f\x9f\xa0\xff\f\n\r\t\v"),
    )


def test_encode_control_characters():
    assert_str_equal(
        "Hello Hello!", encode_control_characters(r"Hello \x48\x65\x6c\x6c\x6f!")
    )
    assert_str_equal(
        r"Escaped \\x48\\x65\\x6c\\x6c\\x6f",
        encode_control_characters(r"Escaped \\x48\\x65\\x6c\\x6c\\x6f"),
    )
    assert_str_equal(
        "Mixed He\\\\x6c\\\\x6co",
        encode_control_characters(r"Mixed \x48\x65\\x6c\\x6c\x6f"),
    )
