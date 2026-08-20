from django.http import QueryDict
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.api.common.query_params import parse_query_bool, parse_query_int


class QueryIntegerParserTests(SimpleTestCase):
    def test_uses_default_only_when_parameter_is_absent_or_blank(self):
        self.assertEqual(parse_query_int(QueryDict(""), "limit", default=5), 5)
        self.assertEqual(parse_query_int(QueryDict("limit="), "limit", default=5), 5)
        self.assertEqual(parse_query_int(QueryDict("limit=0"), "limit", default=5), 0)

    def test_rejects_malformed_integer(self):
        with self.assertRaises(ValidationError) as raised:
            parse_query_int(QueryDict("limit=1.5"), "limit", default=5)

        self.assertIn("limit", raised.exception.detail)

    def test_rejects_values_outside_the_declared_range(self):
        for raw in ("0", "51"):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                parse_query_int(
                    QueryDict(f"page_size={raw}"),
                    "page_size",
                    default=20,
                    min_value=1,
                    max_value=50,
                )

    def test_optional_parameter_can_remain_none(self):
        self.assertIsNone(parse_query_int(QueryDict(""), "exam_id", min_value=1))

    def test_boolean_parser_does_not_turn_typos_into_false(self):
        self.assertTrue(parse_query_bool(QueryDict("active=true"), "active"))
        self.assertFalse(parse_query_bool(QueryDict("active=0"), "active"))
        for raw in ("flase", "yes", "2"):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                parse_query_bool(QueryDict(f"active={raw}"), "active")
