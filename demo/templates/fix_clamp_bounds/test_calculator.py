import unittest
from calculator import clamp


class RepairAcceptanceTests(unittest.TestCase):
    def test_boundaries(self):
        for value, expected in [(-5, 0), (0, 0), (3, 3), (10, 10), (30, 10)]:
            with self.subTest(value=value):
                self.assertEqual(clamp(value, 0, 10), expected)

    def test_negative_and_invalid(self):
        self.assertEqual(clamp(-4, -8, -2), -4)
        with self.assertRaises(ValueError):
            clamp(1, 5, 2)


if __name__ == '__main__':
    unittest.main()
