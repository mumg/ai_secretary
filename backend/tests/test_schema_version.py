from unittest import TestCase

from improver.schema_version import SchemaVersionState, schema_is_compatible


class SchemaVersionTests(TestCase):
    def test_missing_or_old_schema_is_not_compatible(self) -> None:
        self.assertFalse(schema_is_compatible(None, minimum=15))
        self.assertFalse(schema_is_compatible(SchemaVersionState(14, "0014"), minimum=15))

    def test_minimum_and_newer_schema_are_compatible(self) -> None:
        self.assertTrue(schema_is_compatible(SchemaVersionState(15, "0015"), minimum=15))
        self.assertTrue(schema_is_compatible(SchemaVersionState(16, "0016"), minimum=15))
