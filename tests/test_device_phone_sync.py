import pytest

from app.services.device_service import _phone_key, _reported_phone_number


@pytest.mark.parametrize("value", [
    "+1 (202) 555-0106", "+12025550106", "12025550106", "2025550106",
])
def test_complete_nanp_formats_compare_equal(value):
    assert _phone_key(value) == "+12025550106"
    assert _reported_phone_number(" " + value + " ") == value


@pytest.mark.parametrize("value", [
    None, "", "   ", "unknown", "UNKNOWN", "未知", "null", "N/A", "***0106",
    "202555010", "+1202555010", "1202555010", "+120255501067", "+11025550106",
    "+12021550106", "0000000000", "2222222222", "+12222222222", "+0000000000",
    "+9999999", "+123", "123456", "00442079460958", "442079460958",
    "+442079460958 ext 1", "+442079460958;1", "+442079460958#", "+1 202 555 0106a",
    "+１２０２５５５０１０６", "+4420794609581234", "+442079", 1234567890, False,
])
def test_unknown_truncated_masked_or_ambiguous_numbers_are_ignored(value):
    assert _phone_key(value) is None
    assert _reported_phone_number(value) is None


@pytest.mark.parametrize(("value", "key"), [
    ("+44 (20) 7946-0958", "+442079460958"),
    ("+86 138-0013-8000", "+8613800138000"),
    ("+49 30 123456", "+4930123456"),
    ("+358401234567", "+358401234567"),
    ("+4930123", "+4930123"),
    ("+493012345678901", "+493012345678901"),
])
def test_explicit_international_numbers_are_supported(value, key):
    assert _phone_key(value) == key
