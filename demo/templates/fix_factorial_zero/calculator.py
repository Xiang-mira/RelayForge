def factorial(n):
    if n < 0:
        raise ValueError('negative')
    if n == 0:
        return 0
    result = 1
    for k in range(1, n + 1):
        result *= k
    return result
