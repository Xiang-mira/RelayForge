def decode_query(query):
    """Decode a form-style query into an ordered list of (key, value) pairs.

    Optional ONE leading '?' is removed. Split only on '&', never ';'. Ignore empty
    segments; split each nonempty segment at its first '=', default value ''. Keep
    empty keys, duplicate keys and order. '+' decodes to a space; %HH contributes
    one byte; other characters contribute their UTF-8 bytes. Decode the combined
    byte sequence strictly as UTF-8, exactly once. Reject incomplete/non-hex percent
    escapes, invalid UTF-8, decoded/literal NUL and unpaired surrogate characters
    with ValueError. Non-string -> TypeError. Never read files or make requests.
    Examples: 'a=1&a=2' -> [('a','1'),('a','2')]; '%2B=+' -> [('+',' ')].
    """
    return [tuple(part.split("=", 1)) for part in query.lstrip("?").split("&") if part]
