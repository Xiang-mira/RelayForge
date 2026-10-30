import unittest
from calculator import median


class RepairAcceptanceTests(unittest.TestCase):
    def test_even(self):
        self.assertEqual(median([4, 1, 3, 2]), 2.5)
        self.assertEqual(median([-4, -2]), -3)

    def test_odd_and_empty(self):
        self.assertEqual(median([9, 1, 4]), 4)
        self.assertEqual(median([8]), 8)
        with self.assertRaises(ValueError):
            median([])


if __name__ == '__main__':
    unittest.main()
