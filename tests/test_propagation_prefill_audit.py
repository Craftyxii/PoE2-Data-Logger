"""Protect full-reader accuracy denominators and held-review scoring."""

import unittest

from tools.audit_propagation_prefill import score_rows


class PropagationPrefillAuditTests(unittest.TestCase):
    """Count physical socket suggestions once and refuse unaligned recipe rows."""

    def test_held_runes_are_not_counted_as_dropdown_suggestions(self):
        """A withheld result counts as missing even if diagnostic positions remain attached."""
        counts = score_rows([{"full_positions": [2, 5]}],
                            [{"positions": [2, 5], "can_use": False}])
        self.assertEqual((counts["tp"], counts["fp"], counts["fn"], counts["exact_rows"]), (0, 0, 2, 0))
        self.assertEqual(counts["suggested_rows"], 0)

    def test_duplicate_positions_cannot_inflate_accuracy(self):
        """Repeated anchors supply one true socket and leave the missing second socket in the denominator."""
        counts = score_rows([{"full_positions": [2, 5]}],
                            [{"positions": [2, 2], "can_use": True}])
        self.assertEqual((counts["tp"], counts["fp"], counts["fn"]), (1, 0, 1))

    def test_extra_suggested_socket_is_a_false_positive(self):
        """A suggestion at an unmarked socket counts against precision rather than being ignored."""
        counts = score_rows([{"full_positions": [2]}],
                            [{"positions": [2, 5], "can_use": True}])
        self.assertEqual((counts["tp"], counts["fp"], counts["fn"], counts["exact_rows"]), (1, 1, 0, 0))

    def test_unaligned_rows_require_inspection_before_scoring(self):
        """Missing OCR rows cannot silently shift annotations onto later recognized recipes."""
        with self.assertRaisesRegex(ValueError, "row counts differ"):
            score_rows([{"full_positions": [2]}, {"full_positions": [5]}],
                       [{"positions": [2], "can_use": True}])


if __name__ == "__main__":
    unittest.main()
