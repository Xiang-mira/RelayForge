def median(values):
    if not values:
        raise ValueError('empty')
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return values[middle - 1] + values[middle]
