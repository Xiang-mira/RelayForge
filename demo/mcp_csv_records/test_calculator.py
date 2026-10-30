import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("managed_csv", Path(__file__).with_name("calculator.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("text, expected", [
    ("", []), ("a", [["a"]]), ("a,b", [["a", "b"]]),
    (",", [["", ""]]), ("a,", [["a", ""]]), (",,", [["", "", ""]]),
    ("\n", [[""]]), ("\r\n", [[""]]), ("\n\n", [[""], [""]]),
    ("a\n", [["a"]]), ("a\r\nb\n", [["a"], ["b"]]),
    ('"a,b",c', [["a,b", "c"]]), ('""', [[""]]), ('"",', [["", ""]]),
    ('"a""b"', [['a"b']]), ('""""', [['"']]),
    ('"a\nb",c\r\nx,y', [["a\nb", "c"], ["x", "y"]]),
    ('"a\r\nb"', [["a\r\nb"]]), ('"a\rb"', [["a\rb"]]),
    (' a , b ', [[" a ", " b "]]), ('" a "', [[" a "]]),
    ('"x",\r\n', [["x", ""]]), ('雪,☃', [["雪", "☃"]]),
    ('a\tb,c', [["a\tb", "c"]]), ('a\v,b', [["a\v", "b"]]),
])
def test_records(text, expected):
    assert module.parse_records(text) == expected


@pytest.mark.parametrize("text", ['"', '"a', 'a"b', ' "a"', '"a" ', '"a"x',
    '"a""', 'a\rb', '\r', 'a\r\r\n', 'a\x00b', '"a\x00b"', 'a,"b"c'])
def test_reject_malformed(text):
    with pytest.raises(ValueError):
        module.parse_records(text)


@pytest.mark.parametrize("value", [None, 1, b'a,b'])
def test_type(value):
    with pytest.raises(TypeError):
        module.parse_records(value)
