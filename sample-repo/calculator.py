"""A tiny sample module so CodeBase Brain has something to index out of the box."""


def add(a, b):
    """Return the sum of two numbers."""
    return a + b


def multiply(a, b):
    """Return the product of two numbers, implemented via repeated addition."""
    total = 0
    for _ in range(abs(b)):
        total = add(total, a)
    return total if b >= 0 else -total


def main():
    """Entry point: demonstrate the calculator."""
    print(add(2, 3))
    print(multiply(4, 5))


if __name__ == "__main__":
    main()
