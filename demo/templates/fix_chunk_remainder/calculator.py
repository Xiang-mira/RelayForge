def chunks(values, size):
    if size <= 0:
        raise ValueError('invalid size')
    return [values[i:i + size] for i in range(0, len(values) - size + 1, size)]
