import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("managed_query", Path(__file__).with_name("calculator.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("text, expected", [
    ("", []), ("?", []), ("&&", []), ("?a=1&a=2", [("a", "1"), ("a", "2")]),
    ("key", [("key", "")]), ("=v", [("", "v")]), ("=", [("", "")]),
    ("a=b=c", [("a", "b=c")]), ("a=1;;b=2", [("a", "1;;b=2")]),
    ("%2B=+", [("+", " ")]), ("a=%2520", [("a", "%20")]),
    ("a=%26b%3Dc", [("a", "&b=c")]), ("x=%E9%9B%AA", [("x", "雪")]),
    ("x=雪", [("x", "雪")]), ("x=雪%E2%98%83", [("x", "雪☃")]),
    ("??x=y", [("?x", "y")]), ("a=1&&b&", [("a", "1"), ("b", "")]),
    ("x=%f0%9f%98%80", [("x", "😀")]), ("x=%09%0A", [("x", "\t\n")]),
])
def test_query(text, expected):
    assert module.decode_query(text) == expected


@pytest.mark.parametrize("text", ["x=%", "x=%1", "x=%GG", "%xz=a", "x=%FF", "x=%C0%AF",
    "x=%E2%82", "x=%ED%A0%80", "x=%F4%90%80%80", "x=%00", "x=\x00", "x=\ud800"])
def test_invalid(text):
    with pytest.raises(ValueError):
        module.decode_query(text)


@pytest.mark.parametrize("text", [None, 1, b'a=1'])
def test_type(text):
    with pytest.raises(TypeError):
        module.decode_query(text)
