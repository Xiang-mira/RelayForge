import unittest
from calculator import unique


class RepairAcceptanceTests(unittest.TestCase):
    def test_order(self):
        self.assertEqual(unique(['b', 'a', 'b', 'c', 'a']), ['b', 'a', 'c'])
        self.assertEqual(unique([3, 1, 3, 2]), [3, 1, 2])

    def test_empty_and_single(self):
        self.assertEqual(unique([]), [])
        self.assertEqual(unique([1, 1]), [1])


if __name__ == '__main__':
    unittest.main()
