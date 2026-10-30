def split_words(text):
    """Tokenize text without executing anything or expanding variables.

    Outside quotes, spaces/tabs/CR/LF separate words. Backslash quotes the next
    character, except backslash+LF is removed (line continuation, creates no word
    by itself). Single quotes preserve every character until the next single
    quote. Inside double quotes, backslash removes itself only before $, `, ",
    backslash or LF; before any other character keep BOTH backslash and character.
    Quoted/unquoted adjacent pieces concatenate; empty quotes create an empty word.
    # starts a comment only outside quotes AND at the beginning of a word; skip to
    LF, then continue scanning. Otherwise # is literal. No variable, glob or command
    expansion: $HOME and $(x) are data. Reject NUL, unclosed quotes and an unmatched
    final backslash with ValueError. Non-string -> TypeError; empty input -> [].
    """
    return text.split()
