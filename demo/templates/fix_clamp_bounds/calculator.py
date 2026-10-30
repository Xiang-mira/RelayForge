def clamp(value, low, high):
    if low > high:
        raise ValueError('reversed bounds')
    return min(low, max(value, high))
