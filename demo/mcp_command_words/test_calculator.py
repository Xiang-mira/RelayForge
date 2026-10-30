import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("managed_words", Path(__file__).with_name("calculator.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("text, expected", [
    ("", []), (" \t\r\n", []), ("a b", ["a", "b"]),
    ("a\r\nb", ["a", "b"]), ("'a b' c", ["a b", "c"]),
    ('"a b"', ["a b"]), ("''", [""]), ('""', [""]),
    ("a''b\"c\"", ["abc"]), ("'' ''", ["", ""]),
    (r'a\ b', ['a b']), (r'a\\b', [r'a\b']),
    ("'a\\b'", ["a\\b"]), ('"a\\qb"', ["a\\qb"]),
    (r'"\$x\`q\"\\"', ['$x`q"\\']),
    ("a\\\nb", ["ab"]), ("\\\n", []), ('"\\\n"', [""]),
    ('"a\nb"', ["a\nb"]), ("'a\nb'", ["a\nb"]),
    ("# ignore\nnext", ["next"]), ("a # skip\nb", ["a", "b"]),
    ("a#b", ["a#b"]), ('"#"', ["#"]), (r'\#x', ["#x"]),
    ("''#x", ["#x"]), ("#'bad\na", ["a"]),
    ("$HOME $(x) *", ["$HOME", "$(x)", "*"]),
    ("雪 '☃ x'", ["雪", "☃ x"]),
])
def test_words(text, expected):
    assert module.split_words(text) == expected


@pytest.mark.parametrize("text", ["'a", '"a', "a\\", '"a\\', "a\x00b", "'\x00'"])
def test_invalid(text):
    with pytest.raises(ValueError):
        module.split_words(text)


@pytest.mark.parametrize("text", [None, 1, b'a'])
def test_type(text):
    with pytest.raises(TypeError):
        module.split_words(text)
