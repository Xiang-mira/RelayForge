import unittest
from calculator import factorial


class RepairAcceptanceTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(factorial(0), 1)

    def test_values_and_invalid(self):
        self.assertEqual(factorial(1), 1)
        self.assertEqual(factorial(5), 120)
        with self.assertRaises(ValueError):
            factorial(-1)


if __name__ == '__main__':
    unittest.main()
