import copy
import json
from uuid import uuid4

import pytest

from tests.utils import INTERNAL_TOKEN, PayloadGenerator, RequestGenerator
from tests.utils.requisition import ClientRequisition

JSON_HEADERS = {"INTERNAL-TOKEN": INTERNAL_TOKEN, "Content-Type": "application/json"}

BIGINT_MAX = 9223372036854775807


def valid_payload() -> dict:
    return PayloadGenerator.create_pix_payload()


def post_raw(body: str, headers: dict = None):
    if headers is None:
        headers = JSON_HEADERS

    response = ClientRequisition.send("POST", "/pix/transaction", data=body.encode("utf-8"), headers=headers)
    return response.response_status, response.response_json


def with_field(payload: dict, path: str, value) -> dict:
    changed = copy.deepcopy(payload)
    *parents, last = path.split(".")

    node = changed
    for key in parents:
        node = node[key]

    node[last] = value
    return changed


def without_field(payload: dict, path: str) -> dict:
    changed = copy.deepcopy(payload)
    *parents, last = path.split(".")

    node = changed
    for key in parents:
        node = node[key]

    del node[last]
    return changed


def assert_client_error(status: int, response, expected_status: int, expected_code: str = None) -> None:
    assert status != 500, f"500 inesperado: {response}"
    assert status == expected_status, f"esperava {expected_status}, veio {status}: {response}"
    assert isinstance(response, dict) and "code" in response, f"erro sem código: {response}"

    if expected_code is not None:
        assert response["code"] == expected_code, response


REQUIRED_FIELDS = [
    "id",
    "amount",
    "transaction_date",
    "source_account",
    "source_account.branch",
    "source_account.account_number",
    "source_account.opening_date",
    "source_account.owner",
    "source_account.owner.document_number",
    "destination_account",
    "destination_account.participant",
    "destination_account.branch",
    "destination_account.account_number",
    "destination_account.opening_date",
    "destination_account.owner",
    "destination_account.owner.document_number",
    "source",
    "source.device_id",
    "source.channel",
    "source.ip",
]

STRING_FIELDS = [
    field
    for field in REQUIRED_FIELDS
    if field
    not in (
        "amount",
        "source_account",
        "source_account.owner",
        "destination_account",
        "destination_account.owner",
        "source",
    )
]
OBJECT_FIELDS = ["source_account", "source_account.owner", "destination_account", "destination_account.owner", "source"]


class TestBodyFormat:
    @pytest.mark.parametrize(
        "body",
        [
            "",
            "isto não é json",
            "{",
            "{'id': 'aspas simples'}",
            "[]",
            "null",
            '"um texto"',
            "123",
            "true",
            '{"id": "pix-1",}',
        ],
    )
    def test_body_that_is_not_a_pix_object(self, body):
        status, response = post_raw(body)
        assert_client_error(status, response, 400, "QIT000001")

    def test_wrong_content_type(self):
        body = json.dumps(valid_payload())
        status, response = post_raw(body, headers={"INTERNAL-TOKEN": INTERNAL_TOKEN, "Content-Type": "text/plain"})

        assert status != 500, response
        assert status in (201, 400), response

    def test_deeply_nested_json(self):
        depth = 100000
        body = '{"id": ' + "[" * depth + "]" * depth + "}"

        status, response = post_raw(body)
        assert_client_error(status, response, 400, "QIT000001")

    def test_huge_string(self):
        payload = with_field(valid_payload(), "source.device_id", "x" * 2_000_000)

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_numbers_that_are_not_json(self, literal):
        for path in ["amount", "source.geolocation.latitude", "source.geolocation.longitude"]:
            marker = "__NUMERO__"
            body = json.dumps(with_field(valid_payload(), path, marker)).replace(f'"{marker}"', literal)

            status, response = post_raw(body)
            assert_client_error(status, response, 400, "QIT000001")

    def test_duplicated_keys_keep_working(self):
        payload = valid_payload()
        body = json.dumps(payload)[:-1] + ', "amount": 7000}'

        status, response = post_raw(body)
        assert status == 201, response


class TestFieldsMissingOrWrongType:
    @pytest.mark.parametrize("path", REQUIRED_FIELDS)
    def test_missing_required_field(self, path):
        status, response = RequestGenerator.POST_pix_transaction(without_field(valid_payload(), path))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("path", REQUIRED_FIELDS)
    def test_null_field(self, path):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, None))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("path", STRING_FIELDS)
    @pytest.mark.parametrize("wrong_value", [123, 1.5, True, [], {}])
    def test_string_field_with_other_type(self, path, wrong_value):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, wrong_value))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("path", OBJECT_FIELDS)
    @pytest.mark.parametrize("wrong_value", ["texto", 123, [], True])
    def test_object_field_with_other_type(self, path, wrong_value):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, wrong_value))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize(
        "path",
        ["campo_novo", "source_account.campo_novo", "source_account.owner.level", "destination_account.pix_key", "source.street_mode"],
    )
    def test_field_outside_the_contract(self, path):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, "x"))
        assert_client_error(status, response, 400, "QIT000001")


class TestFieldValues:
    @pytest.mark.parametrize("amount", [0, -1, -150000, 1.5, "150000", BIGINT_MAX + 1, 10**30])
    def test_invalid_amount(self, amount):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "amount", amount))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("amount", [1, BIGINT_MAX])
    def test_amount_at_the_edges_is_accepted(self, amount):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "amount", amount))
        assert status == 201, response

    @pytest.mark.parametrize("path", ["transaction_date", "source_account.opening_date", "destination_account.opening_date"])
    @pytest.mark.parametrize(
        "raw_date",
        [
            "17/10/2026 22:15",
            "2026-10-17",
            "2026-10-17T22:15:00",
            "2026-10-17 22:15:00-03:00",
            "2026-10-17T22:15-03:00",
            "2026-10-17T22:15:00.1234567-03:00",
            "2026-10-17T22:15:00-0300",
            "hoje",
            "",
        ],
    )
    def test_malformed_dates(self, path, raw_date):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, raw_date))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("path", ["transaction_date", "source_account.opening_date", "destination_account.opening_date"])
    @pytest.mark.parametrize(
        "raw_date",
        [
            "2026-02-30T10:00:00-03:00",
            "2026-13-01T10:00:00-03:00",
            "2026-10-17T24:00:00-03:00",
            "2026-10-17T22:60:00-03:00",
            "2026-10-17T22:15:60-03:00",
            "2026-10-17T22:15:00+25:00",
            "0000-01-01T00:00:00-03:00",
            "0001-01-01T00:00:00+14:00",
            "9999-12-31T23:59:59-14:00",
        ],
    )
    def test_impossible_or_out_of_range_dates(self, path, raw_date):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, raw_date))
        assert_client_error(status, response, 422, "QIT002005")

    @pytest.mark.parametrize(
        "raw_date",
        ["2020-11-15T23:59:59-03:00", "1999-01-01T10:00:00-03:00", "2101-01-01T10:00:00-03:00"],
    )
    def test_transaction_date_outside_the_pix_era(self, raw_date):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "transaction_date", raw_date))
        assert_client_error(status, response, 422, "QIT002005")

    @pytest.mark.parametrize("raw_date", ["2026-10-17T22:15:00Z", "2026-10-17T22:15:00.5+00:00", "2020-11-16T00:00:00-03:00"])
    def test_valid_date_variations_are_accepted(self, raw_date):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "transaction_date", raw_date))
        assert status == 201, response

    @pytest.mark.parametrize("path", ["source_account.owner.document_number", "destination_account.owner.document_number"])
    @pytest.mark.parametrize(
        "document",
        [
            "123.456.789-0",
            "123.456.789-099",
            "1234567890",
            "123456789012",
            "123.456.78909",
            "abc.def.ghi-jk",
            "١٢٣.٤٥٦.٧٨٩-٠٩",
            "１２３４５６７８９０９",
            "123.456.789-09 ",
            "",
        ],
    )
    def test_malformed_document(self, path, document):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, document))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("value", ["", "abc", "1234567", "123456789", "١٢٣٤٥٦٧٨", "1234 567"])
    def test_malformed_participant(self, value):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "destination_account.participant", value))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("path", ["source_account.branch", "destination_account.branch"])
    @pytest.mark.parametrize("value", ["", "abc", "12345678901", "١٢٣٤", "12 34"])
    def test_malformed_branch(self, path, value):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, value))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize(
        "path", ["id", "source.device_id", "source.channel", "source_account.account_number", "destination_account.account_number"]
    )
    @pytest.mark.parametrize("value", ["com\u0000nulo", "quebra\nde linha", "tab\tulação", "\u001b[31m"])
    def test_control_characters(self, path, value):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, value))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize(
        "path, max_length",
        [("id", 100), ("source.device_id", 100), ("source.channel", 20), ("source.ip", 45)],
    )
    def test_strings_over_the_limit(self, path, max_length):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), path, "1" * (max_length + 1)))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("pix_id", ["pix/000123", "pix 000123", "pix?x=1", "pix#1", "../../etc", "pix%00", "píx-ç"])
    def test_id_that_could_not_be_queried_later(self, pix_id):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "id", pix_id))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("ip", ["", "localhost", "999.999.999.999.999.999.999.999.999.999.999.9999", "1.2.3.4 ", "<script>"])
    def test_malformed_ip(self, ip):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "source.ip", ip))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("ip", ["198.51.100.23", "2001:db8::1", "::1"])
    def test_ipv4_and_ipv6_are_accepted(self, ip):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "source.ip", ip))
        assert status == 201, response

    @pytest.mark.parametrize(
        "geolocation",
        [
            {"latitude": 91, "longitude": 0},
            {"latitude": -91, "longitude": 0},
            {"latitude": 0, "longitude": 181},
            {"latitude": 0, "longitude": -181},
            {"latitude": "-23.56", "longitude": "-46.65"},
            {"latitude": -23.56},
            {"longitude": -46.65},
            {},
            {"latitude": -23.56, "longitude": -46.65, "altitude": 760},
            [],
            None,
        ],
    )
    def test_malformed_geolocation(self, geolocation):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "source.geolocation", geolocation))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("geolocation", [{"latitude": 90, "longitude": 180}, {"latitude": -90, "longitude": -180}])
    def test_geolocation_at_the_edges_is_accepted(self, geolocation):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "source.geolocation", geolocation))
        assert status == 201, response

    def test_geolocation_is_optional(self):
        status, response = RequestGenerator.POST_pix_transaction(without_field(valid_payload(), "source.geolocation"))
        assert status == 201, response

    def test_unicode_in_free_text_fields_is_accepted(self):
        payload = with_field(valid_payload(), "source.device_id", "aparelho-ção-😀")
        payload = with_field(payload, "source.channel", "aplicação")

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response

    def test_cnpj_on_both_sides_is_accepted(self):
        payload = with_field(valid_payload(), "source_account.owner.document_number", "11.222.333/0001-81")
        payload = with_field(payload, "destination_account.owner.document_number", "11222333000181")

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response


class TestPathParameters:
    @pytest.mark.parametrize(
        "pix_id",
        [
            "%00",
            "pix%00abc",
            "%2e%2e%2f%2e%2e",
            "pix%2F000123",
            "a" * 5000,
            "%F0%9F%98%80",
            "pix%20000123",
            "%27%20OR%20%271%27%3D%271",
            "%E2%80%AE",
        ],
    )
    def test_get_with_strange_id(self, pix_id):
        response = ClientRequisition.send("GET", f"/pix/transaction/{pix_id}", headers={"INTERNAL-TOKEN": INTERNAL_TOKEN})

        assert response.response_status != 500, response.response_json
        assert response.response_status == 404, response.response_json

    @pytest.mark.parametrize("pix_id", ["%00", "a" * 5000, "%27%20OR%20%271%27%3D%271"])
    def test_two_factor_with_strange_id(self, pix_id):
        response = ClientRequisition.send(
            "POST",
            f"/pix/transaction/{pix_id}/two-factor",
            payload={"result": "confirmed"},
            headers={"INTERNAL-TOKEN": INTERNAL_TOKEN},
        )

        assert response.response_status != 500, response.response_json
        assert response.response_status == 404, response.response_json


class TestTwoFactorBody:
    @pytest.mark.parametrize(
        "body",
        ["", "não é json", "[]", "null", '{"result": null}', '{"result": "CONFIRMED"}', '{"result": 1}', '{"result": "confirmed", "x": 1}', "{}"],
    )
    def test_malformed_two_factor_body(self, body):
        pix_id = f"pix-{uuid4()}"
        response = ClientRequisition.send(
            "POST", f"/pix/transaction/{pix_id}/two-factor", data=body.encode("utf-8"), headers=JSON_HEADERS
        )

        assert_client_error(response.response_status, response.response_json, 400, "QIT000001")


class TestMethodsAndRoutes:
    @pytest.mark.parametrize(
        "method, endpoint",
        [
            ("GET", "/pix/transaction"),
            ("PUT", "/pix/transaction"),
            ("DELETE", "/pix/transaction"),
            ("PATCH", "/pix/transaction/pix-1"),
            ("PUT", "/pix/transaction/pix-1"),
            ("GET", "/pix/transaction/pix-1/two-factor"),
        ],
    )
    def test_method_not_allowed(self, method, endpoint):
        response = ClientRequisition.send(method, endpoint, headers={"INTERNAL-TOKEN": INTERNAL_TOKEN})
        assert_client_error(response.response_status, response.response_json, 405, "QIT000405")

    @pytest.mark.parametrize("endpoint", ["/pix", "/pix/transactions", "/pix/transaction/pix-1/two-factor/extra"])
    def test_unknown_route(self, endpoint):
        response = ClientRequisition.send("GET", endpoint, headers={"INTERNAL-TOKEN": INTERNAL_TOKEN})
        assert_client_error(response.response_status, response.response_json, 404, "QIT000404")

    @pytest.mark.parametrize("headers", [{}, {"INTERNAL-TOKEN": ""}, {"INTERNAL-TOKEN": "errado"}, {"internal-token": "errado"}])
    def test_without_a_valid_token(self, headers):
        status, response = RequestGenerator.POST_pix_transaction(valid_payload(), headers=headers)
        assert_client_error(status, response, 403, "QIT000002")
