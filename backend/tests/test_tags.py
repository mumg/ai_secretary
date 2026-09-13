from unittest import TestCase

from pydantic import ValidationError

from improver.schemas import TagWrite


class TagSchemaTests(TestCase):
    def test_tag_name_is_trimmed_and_internal_whitespace_is_collapsed(self) -> None:
        payload = TagWrite(name="  Важные   клиенты  ")

        self.assertEqual(payload.name, "Важные клиенты")

    def test_empty_tag_name_is_rejected_after_normalization(self) -> None:
        with self.assertRaises(ValidationError):
            TagWrite(name="   ")
