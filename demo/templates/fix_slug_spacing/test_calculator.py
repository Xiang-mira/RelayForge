import unittest
from calculator import slug


class RepairAcceptanceTests(unittest.TestCase):
    def test_repeated_whitespace(self):
        self.assertEqual(slug('  Hello   World '), 'hello-world')
        self.assertEqual(slug('Hello\tWorld\nAgain'), 'hello-world-again')

    def test_empty_and_simple(self):
        self.assertEqual(slug('   '), '')
        self.assertEqual(slug('ALPHA'), 'alpha')


if __name__ == '__main__':
    unittest.main()
