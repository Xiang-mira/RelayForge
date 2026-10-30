import unittest
from calculator import chunks


class RepairAcceptanceTests(unittest.TestCase):
    def test_remainder(self):
        self.assertEqual(chunks([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])
        self.assertEqual(chunks([1], 3), [[1]])

    def test_empty_exact_and_invalid(self):
        self.assertEqual(chunks([], 2), [])
        self.assertEqual(chunks([1, 2, 3, 4], 2), [[1, 2], [3, 4]])
        with self.assertRaises(ValueError):
            chunks([1], 0)


if __name__ == '__main__':
    unittest.main()
