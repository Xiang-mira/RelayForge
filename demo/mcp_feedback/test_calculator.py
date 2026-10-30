import unittest

from calculator import shipping_fee


class ShippingTests(unittest.TestCase):
    def test_small_order_costs_eight(self):
        self.assertEqual(shipping_fee(25), 8)

    def test_exact_threshold_is_free(self):
        self.assertEqual(shipping_fee(100), 0, "exact threshold must ship free")

    def test_large_order_is_free(self):
        self.assertEqual(shipping_fee(150), 0)
