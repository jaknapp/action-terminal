import json

from action.app.str_utils import diff_str


def assert_str_equal(expected: str, actual: str, message: str | None = None) -> None:
    the_diff = diff_str(expected, actual)
    the_diff_message = the_diff if message is None else f"{message}\n{the_diff}"
    assert not the_diff, the_diff_message


def assert_dicts_json_equal(
    expected: dict, actual: dict, message: str | None = None
) -> None:
    expected_str = json.dumps(expected, indent=2)
    actual_str = json.dumps(actual, indent=2)
    assert_str_equal(expected_str, actual_str, message)


def assert_login_messages(response_dict: dict) -> None:
    login_messages = {
        pid: ("" if process.get("login_message") else None)
        for pid, process in response_dict["processes"].items()
    }
    expected_login_messages = {pid: "" for pid in response_dict["processes"].keys()}
    assert (
        login_messages == expected_login_messages
    ), f"Login messages not found in {response_dict}"
    for process in response_dict["processes"].values():
        process["login_message"] = ""
