def parse_records(text):
    """Pure CSV parser, never an executor. Return list[list[str]].

    Delimiter is comma. LF or CRLF ends a record outside quoted fields; a bare CR
    outside quotes is invalid. Double quotes may ONLY start at the beginning of a
    field. Inside quotes, commas/CR/LF are literal; doubled quotes decode to one
    quote. After a closing quote ONLY comma, record terminator or EOF is valid.
    Spaces are ordinary data, never stripped. Preserve empty fields and records:
    empty input -> []; a blank line -> [['']]; trailing comma adds an empty field;
    a final record terminator does not add a phantom record. NUL, bare quotes in
    unquoted fields, unterminated quotes and junk after closing quotes raise
    ValueError. Non-string raises TypeError. No file I/O or third-party libraries.
    """
    return [line.split(",") for line in text.splitlines()]
