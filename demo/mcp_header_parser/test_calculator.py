"""Trusted pytest acceptance cases; the Agent can read, but cannot rewrite this file."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("managed_parser", Path(__file__).with_name("calculator.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
parse_parameters = module.parse_parameters


@pytest.mark.parametrize("text, expected", [
    ("", {}),
    (" \t ", {}),
    ("Charset=utf-8", {"charset": "utf-8"}),
    ("  X-Mode = fast ; limit_2=10  ", {"x-mode": "fast", "limit_2": "10"}),
    ('name="a;b"; version=2', {"name": "a;b", "version": "2"}),
    ('name="a=b;c"', {"name": "a=b;c"}),
    ('name="  preserve me  "', {"name": "  preserve me  "}),
    ('name=""', {"name": ""}),
    (r'name="a\"b\\c"', {"name": 'a"b\\c'}),
    ('a=one=two', {"a": "one=two"}),
])
def test_valid_parameters(text, expected):
    assert parse_parameters(text) == expected


@pytest.mark.parametrize("text", [
    "A=1; a=2", "a=1;A=1", "a=1;", ";a=1", "a=1;;b=2",
    "=value", "9key=value", "bad key=value", "missing", "a=",
    'a="unterminated', 'a="ok"junk', 'a=un"quoted',
    r'a="unsupported\q"', r'a=bare\escape', "a=one\r\nb=two",
])
def test_invalid_parameters(text):
    with pytest.raises(ValueError):
        parse_parameters(text)


@pytest.mark.parametrize("text", [None, 7, ["a=1"]])
def test_non_string_input(text):
    with pytest.raises(TypeError):
        parse_parameters(text)
