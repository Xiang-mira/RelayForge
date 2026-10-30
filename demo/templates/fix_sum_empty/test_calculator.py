import unittest
from calculator import total


class RepairAcceptanceTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(total([]), 0)

    def test_mixed(self):
        self.assertEqual(total([2, -3, 5]), 4)
        self.assertEqual(total([0]), 0)


if __name__ == '__main__':
    unittest.main()
