import unittest
import uuid

from politica_erd.ids import (
    candidacy_id,
    contest_id,
    deterministic_uuid,
    election_chamber_id,
    election_id,
    source_revision_id,
)


class IdentifierTests(unittest.TestCase):
    def test_readable_ids(self):
        election = election_id("fed", "2025-05-03", "general")
        self.assertEqual(election, "election_fed_2025_05_03_general")
        self.assertEqual(election_chamber_id(election, "house"), "election_chamber_fed_2025_05_03_general_house")
        self.assertEqual(
            contest_id(election, "house", "105", "Bennelong"),
            "contest_fed_2025_05_03_general_house_105",
        )

    def test_uuid_is_deterministic(self):
        first = candidacy_id("contest_example", "12345")
        second = candidacy_id("contest_example", "12345")
        self.assertEqual(first, second)
        self.assertIsInstance(first, uuid.UUID)

    def test_uuid_normalisation(self):
        self.assertEqual(deterministic_uuid("Fact", " A  B "), deterministic_uuid("fact", "a b"))

    def test_hash_validation(self):
        value = source_revision_id("source_file_test", "a" * 64)
        self.assertTrue(value.startswith("source_revision_"))
        with self.assertRaises(ValueError):
            source_revision_id("source_file_test", "not-a-hash")


if __name__ == "__main__":
    unittest.main()

