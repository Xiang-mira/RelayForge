import unittest
from calculator import is_leap


class RepairAcceptanceTests(unittest.TestCase):
    def test_centuries(self):
        self.assertFalse(is_leap(1900))
        self.assertTrue(is_leap(2000))
        self.assertFalse(is_leap(2100))

    def test_regular(self):
        self.assertTrue(is_leap(2024))
        self.assertFalse(is_leap(2023))


if __name__ == '__main__':
    unittest.main()
