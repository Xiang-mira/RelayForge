def parse_parameters(text):
    """Parse semicolon-separated key=value parameters into a case-insensitive map.

    Keys match [A-Za-z][A-Za-z0-9_-]* and are returned lower-case. Trim outer
    spaces/tabs. Values may be unquoted nonempty strings or double-quoted strings
    (including empty). Inside quotes, semicolons are literal, and backslash may
    escape only a double quote or another backslash. Keep quoted inner whitespace.
    Reject duplicates ignoring case, CR/LF, empty segments, invalid keys, missing
    '=', unclosed quotes, trailing junk after quotes and unsupported escapes with
    ValueError. Empty/whitespace-only input returns {}. Non-string input raises
    TypeError. Do not modify the caller's input or the trusted tests.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not text.strip():
        return {}
    return {key.strip().lower(): value.strip().strip('"')
            for key, value in (part.split("=", 1) for part in text.split(";"))}
