import unittest
from calculator import search


class RepairAcceptanceTests(unittest.TestCase):
    def test_single_and_edges(self):
        self.assertEqual(search([7], 7), 0)
        self.assertEqual(search([1, 3, 5, 7], 7), 3)
        self.assertEqual(search([1, 3, 5, 7], 1), 0)

    def test_missing_and_middle(self):
        self.assertEqual(search([], 5), -1)
        self.assertEqual(search([1, 3, 5], 4), -1)
        self.assertEqual(search([1, 3, 5], 3), 1)


if __name__ == '__main__':
    unittest.main()
