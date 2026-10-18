import copy
import http.client
import json
import socket
from datetime import timedelta
from os import environ
from uuid import uuid4

import pytest

from tests.utils import (
    DAYTIME,
    INTERNAL_TOKEN,
    NIGHTTIME,
    ObjectGenerator,
    PayloadGenerator,
    RandomGenerator,
    RequestGenerator,
)
from tests.utils.requisition import ClientRequisition

JSON_HEADERS = {"INTERNAL-TOKEN": INTERNAL_TOKEN, "Content-Type": "application/json"}

BIGINT_MAX = 9223372036854775807

MAX_DESCRIPTION_LENGTH = 503

API_HOST = environ.get("SERVER_LOCALHOST", "0.0.0.0")
API_PORT = int(environ.get("API_PORT", "3000"))


def valid_payload() -> dict:
    return PayloadGenerator.create_pix_payload()


def post_raw(body: str, headers: dict = None):
    if headers is None:
        headers = JSON_HEADERS

    response = ClientRequisition.send("POST", "/pix/transaction", data=body.encode("utf-8"), headers=headers)
    return response.response_status, response.response_json


def post_with_literal(payload: dict, path: str, literal: str):
    marker = "__LITERAL__"
    body = json.dumps(with_field(payload, path, marker)).replace(f'"{marker}"', literal)
    return post_raw(body)


def raw_request(method: str, path: str, body: bytes = b"", extra_headers: bytes = b"") -> bytes:
    head = f"{method} {path} HTTP/1.1\r\nHost: api\r\nINTERNAL-TOKEN: {INTERNAL_TOKEN}\r\n".encode()
    tail = f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
    return head + extra_headers + tail + body


def send_raw_http(request: bytes) -> tuple:
    connection = socket.create_connection((API_HOST, API_PORT))
    connection.settimeout(10)
    connection.sendall(request)

    received = b""
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            break
        received += chunk
    connection.close()

    head, _separator, body = received.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])

    if body.startswith(b"{"):
        return status, json.loads(body)

    return status, body


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

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
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
    @pytest.mark.parametrize("amount", [0, -1, -150000, 1.5, "150000", BIGINT_MAX + 1, 10**30, True, False])
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
            "2026-10-17T22:15:00+14:01",
            "2026-10-17T22:15:00-12:01",
            "2026-10-17T22:15:00+15:00",
            "2026-10-17T22:15:00+23:59",
            "2026-10-17T22:15:00-23:59",
            "2026-02-29T10:00:00-03:00",
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

    @pytest.mark.parametrize(
        "raw_date",
        [
            "2026-10-17T22:15:00Z",
            "2026-10-17T22:15:00.5+00:00",
            "2020-11-16T00:00:00-03:00",
            "2026-10-17T22:15:00+14:00",
            "2026-10-17T22:15:00-12:00",
            "2026-10-17T22:15:00-00:00",
            "2028-02-29T10:00:00-03:00",
            "2100-12-31T23:59:59-03:00",
        ],
    )
    def test_valid_date_variations_are_accepted(self, raw_date):
        payload = with_field(valid_payload(), "transaction_date", raw_date)
        payload = with_field(payload, "source_account.opening_date", "2019-01-01T10:00:00-03:00")
        payload = with_field(payload, "destination_account.opening_date", "2019-01-01T10:00:00-03:00")

        status, response = RequestGenerator.POST_pix_transaction(payload)
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

    @pytest.mark.parametrize(
        "pix_id", ["pix/000123", "pix 000123", "pix?x=1", "pix#1", "../../etc", "pix%00", "píx-ç", ".", "..", "a" * 101]
    )
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


class TestEncodingAndUnicode:
    @pytest.mark.parametrize("path", STRING_FIELDS)
    def test_lone_surrogate_in_any_text_field(self, path):
        status, response = post_with_literal(valid_payload(), path, '"a\\ud800b"')
        assert_client_error(status, response, 400, "QIT000001")

    def test_lone_surrogate_in_a_field_name(self):
        body = json.dumps(valid_payload())[:-1] + ', "\\udc00": 1}'

        status, response = post_raw(body)
        assert_client_error(status, response, 400, "QIT000001")

    def test_lone_surrogate_in_two_factor(self):
        response = ClientRequisition.send(
            "POST", f"/pix/transaction/pix-{uuid4()}/two-factor", data=b'{"result": "\\ud800"}', headers=JSON_HEADERS
        )
        assert_client_error(response.response_status, response.response_json, 400, "QIT000001")

    def test_body_that_is_not_utf8(self):
        body = json.dumps(with_field(valid_payload(), "source.channel", "__X__")).encode().replace(b"__X__", b"\xff\xfe")

        response = ClientRequisition.send("POST", "/pix/transaction", data=body, headers=JSON_HEADERS)
        assert_client_error(response.response_status, response.response_json, 400, "QIT000001")

    def test_latin1_body(self):
        body = json.dumps(with_field(valid_payload(), "source.channel", "aplicação"), ensure_ascii=False).encode("latin-1")
        headers = {**JSON_HEADERS, "Content-Type": "application/json; charset=latin-1"}

        response = ClientRequisition.send("POST", "/pix/transaction", data=body, headers=headers)
        assert_client_error(response.response_status, response.response_json, 400, "QIT000001")

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig"])
    def test_json_in_utf16_or_with_bom_is_understood(self, encoding):
        body = json.dumps(valid_payload()).encode(encoding)

        response = ClientRequisition.send("POST", "/pix/transaction", data=body, headers=JSON_HEADERS)
        assert response.response_status == 201, response.response_json

    def test_escaped_unicode_is_the_same_pix(self):
        payload = with_field(valid_payload(), "source.channel", "app")
        first_status, first = RequestGenerator.POST_pix_transaction(payload)

        status, again = post_raw(json.dumps(payload).replace('"app"', '"\\u0061pp"'))

        assert first_status == 201, first
        assert status == 200, again
        assert again["key"] == first["key"]

    def test_error_description_never_echoes_the_whole_input(self):
        status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "source.device_id", "x" * 2_000_000))

        assert_client_error(status, response, 400, "QIT000001")
        assert len(response["description"]) <= MAX_DESCRIPTION_LENGTH


class TestNumbersAtTheEdge:
    @pytest.mark.parametrize("literal", ["5000.0", "1E2", "1.5e3", "9.2e18", "1e-400", "-0", "0.0"])
    def test_amount_must_be_a_json_integer(self, literal):
        status, response = post_with_literal(valid_payload(), "amount", literal)
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("path", ["amount", "source.geolocation.latitude"])
    def test_number_with_thousands_of_digits(self, path):
        status, response = post_with_literal(valid_payload(), path, "1" + "0" * 5000)
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("literal", ["1e-400", "-0.0", "89.9999999999999", "-23.5600000000000000001"])
    def test_odd_but_valid_coordinates_are_accepted(self, literal):
        status, response = post_with_literal(valid_payload(), "source.geolocation.latitude", literal)
        assert status == 201, response

    def test_resend_with_an_integer_written_as_float_is_refused(self):
        payload = valid_payload()
        RequestGenerator.POST_pix_transaction(payload)

        status, response = post_with_literal(payload, "amount", f"{payload['amount']}.0")
        assert_client_error(status, response, 400, "QIT000001")


class TestImpossibleData:
    @pytest.mark.parametrize("path", ["source_account.opening_date", "destination_account.opening_date"])
    def test_account_opened_after_the_pix_is_a_fact(self, path):
        payload = with_field(valid_payload(), path, "2100-12-31T23:59:59-03:00")

        status, response = RequestGenerator.POST_pix_transaction(payload)

        assert status == 201, response
        assert response["status"] == "rejected"
        assert ObjectGenerator.decision(response)["reason"] == "blocking_rule"
        assert ObjectGenerator.decision(response)["rules"] == ["account_opened_after_pix"]

        signal = next(signal for signal in response["metadata"]["signals"] if signal["rule"] == "account_opened_after_pix")
        assert signal["observed"] == {"opened_after_pix": [path]}

    def test_account_opened_at_the_same_moment_is_accepted(self):
        payload = valid_payload()
        payload = with_field(payload, "destination_account.opening_date", payload["transaction_date"])
        payload = with_field(payload, "source_account.opening_date", payload["transaction_date"])

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response

    def test_pix_rejected_by_a_fact_is_recorded_and_queryable(self):
        payload = with_field(valid_payload(), "source_account.opening_date", "2100-12-31T23:59:59-03:00")
        _status, analysis = RequestGenerator.POST_pix_transaction(payload)

        status, saved = RequestGenerator.GET_pix_transaction(payload["id"])

        assert status == 200, saved
        assert saved["key"] == analysis["key"]
        assert saved["status"] == "rejected"

    def test_ids_made_of_symbols_can_be_queried(self):
        for pix_id in [f"...{uuid4().hex[:6]}", f":{uuid4().hex[:6]}", f"-{uuid4().hex[:6]}"]:
            status, response = RequestGenerator.POST_pix_transaction(with_field(valid_payload(), "id", pix_id))
            assert status == 201, response

            status, saved = RequestGenerator.GET_pix_transaction(pix_id)
            assert status == 200, saved
            assert saved["key"] == response["key"]


class TestEngineExtremes:
    @pytest.mark.parametrize(
        "known, here",
        [((-86.62, -179.0), (86.62, 1.0)), ((45, 0), (-45, 180)), ((90, 0), (-90, 0)), ((0, 0), (0, 180))],
    )
    def test_antipodal_places_do_not_break_the_distance(self, known, here):
        payer = RandomGenerator.generate_cpf()

        ObjectGenerator.send_pix(payer_document=payer, geolocation={"latitude": known[0], "longitude": known[1]})
        far = ObjectGenerator.send_pix(
            payer_document=payer,
            geolocation={"latitude": here[0], "longitude": here[1]},
            transaction_date=DAYTIME + timedelta(hours=2),
        )

        assert "far_from_usual_places" in ObjectGenerator.fired_rules(far)

    def test_largest_amounts_add_up_without_overflow(self):
        payer = RandomGenerator.generate_cpf()

        for days_ago in [3, 2, 1]:
            ObjectGenerator.send_pix(payer_document=payer, amount=BIGINT_MAX, transaction_date=DAYTIME - timedelta(days=days_ago))
        for minute in range(3):
            ObjectGenerator.send_pix(payer_document=payer, amount=BIGINT_MAX, transaction_date=DAYTIME + timedelta(minutes=minute))

        at_night = ObjectGenerator.send_pix(payer_document=payer, amount=BIGINT_MAX, transaction_date=NIGHTTIME)
        assert at_night["status"] in ("approved", "two_factor_required", "rejected")

    def test_company_paying_at_night_many_times(self):
        for minute in range(6):
            analysis = ObjectGenerator.send_pix(
                payer_document="11.222.333/0001-81", amount=200000, transaction_date=NIGHTTIME + timedelta(minutes=minute)
            )
            assert "night_limit_exceeded" not in ObjectGenerator.fired_rules(analysis)

    def test_paying_yourself(self):
        payload = valid_payload()
        payload["destination_account"]["owner"]["document_number"] = payload["source_account"]["owner"]["document_number"]

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert status == 201, response

    @pytest.mark.parametrize("document", ["000.000.000-00", "111.111.111-11", "00000000000000", "99.999.999/9999-99"])
    def test_documents_that_cannot_exist_are_rejected_not_broken(self, document):
        analysis = ObjectGenerator.send_pix(payer_document=document)

        assert analysis["status"] == "rejected"
        assert ObjectGenerator.decision(analysis)["rules"] == ["invalid_document_number"]


ROUTES = [
    "/",
    "/health_check",
    "/pix/transaction",
    "/pix/transaction/pix-inexistente",
    "/pix/transaction/pix-inexistente/two-factor",
]


class TestHttpLayer:
    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"])
    @pytest.mark.parametrize("route", ROUTES)
    def test_no_method_route_or_body_breaks_the_api(self, method, route):
        for body in [b"", b"{}", b"[]", b"null", b"nao e json", b'{"result": "confirmed"}']:
            response = ClientRequisition.send(method, route, data=body, headers=JSON_HEADERS)

            assert response.response_status < 500, (method, route, body, response.response_json)
            if response.response_status >= 400:
                assert isinstance(response.response_json, dict) and "code" in response.response_json, (method, route, body)

    @pytest.mark.parametrize("route", ROUTES)
    def test_head_is_answered_without_error(self, route):
        response = ClientRequisition.send("HEAD", route, headers=JSON_HEADERS)
        assert response.response_status < 500

    @pytest.mark.parametrize(
        "content_type",
        ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "application/xml", "", "*/*", "garbage"],
    )
    def test_body_that_is_not_declared_as_json(self, content_type):
        headers = {"INTERNAL-TOKEN": INTERNAL_TOKEN, "Content-Type": content_type}
        response = ClientRequisition.send("POST", "/pix/transaction", data=json.dumps(valid_payload()).encode(), headers=headers)

        assert_client_error(response.response_status, response.response_json, 400, "QIT000001")

    @pytest.mark.parametrize("content_type", ["application/vnd.api+json", "application/json; charset=utf-8", "application/json;;;"])
    def test_json_variants_are_accepted(self, content_type):
        headers = {"INTERNAL-TOKEN": INTERNAL_TOKEN, "Content-Type": content_type}
        response = ClientRequisition.send("POST", "/pix/transaction", data=json.dumps(valid_payload()).encode(), headers=headers)

        assert response.response_status == 201, response.response_json

    def test_list_of_pix_instead_of_one(self):
        status, response = post_raw(json.dumps([valid_payload()] * 100))
        assert_client_error(status, response, 400, "QIT000001")

    def test_megabytes_of_whitespace(self):
        status, response = post_raw(" " * 5_000_000 + "{}")
        assert_client_error(status, response, 400, "QIT000001")

    def test_many_unknown_fields(self):
        payload = valid_payload()
        payload["extra"] = {str(index): index for index in range(100_000)}

        status, response = RequestGenerator.POST_pix_transaction(payload)
        assert_client_error(status, response, 400, "QIT000001")
        assert len(response["description"]) <= MAX_DESCRIPTION_LENGTH

    def test_garbage_query_string_is_ignored(self):
        response = ClientRequisition.send(
            "POST", "/pix/transaction?x=%ZZ&&=&a[]=1&id=outro", payload=valid_payload(), headers=JSON_HEADERS
        )
        assert response.response_status == 201, response.response_json

    @pytest.mark.parametrize("pix_id", ["%ff%fe", "%ed%a0%80", "%c3%a7"])
    def test_path_with_bytes_that_are_not_valid_text(self, pix_id):
        status, response = send_raw_http(raw_request("GET", f"/pix/transaction/{pix_id}"))
        assert_client_error(status, response, 404, "QIT002002")

        status, response = send_raw_http(raw_request("POST", f"/pix/transaction/{pix_id}/two-factor", b'{"result": "confirmed"}'))
        assert_client_error(status, response, 404, "QIT002002")

    def test_trailing_slash_redirects_to_the_route(self):
        response = ClientRequisition.send("GET", "/pix/transaction/pix-inexistente/", headers=JSON_HEADERS)

        assert response.response.history and response.response.history[0].status_code == 307
        assert_client_error(response.response_status, response.response_json, 404, "QIT002002")

    def test_chunked_body(self):
        body = json.dumps(valid_payload()).encode()
        request = (
            f"POST /pix/transaction HTTP/1.1\r\nHost: api\r\nINTERNAL-TOKEN: {INTERNAL_TOKEN}\r\n"
            "Content-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        ).encode()
        request += f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"

        status, response = send_raw_http(request)
        assert status == 201, response

    def test_duplicated_token_header_uses_the_first(self):
        status, response = send_raw_http(raw_request("POST", "/pix/transaction", b"{}", b"INTERNAL-TOKEN: errado\r\n"))
        assert_client_error(status, response, 400, "QIT000001")

    @pytest.mark.parametrize("token", ["x" * 10_000, "çãõ".encode().decode("latin-1"), INTERNAL_TOKEN.upper()])
    def test_strange_tokens(self, token):
        status, response = RequestGenerator.POST_pix_transaction(valid_payload(), headers={"INTERNAL-TOKEN": token})
        assert_client_error(status, response, 403, "QIT000002")

    @pytest.mark.parametrize(
        "request_id, echoed",
        [
            ("pedido-123", True),
            ("a" * 64, True),
            ("a" * 65, False),
            ("../x", False),
            ("çã".encode().decode("latin-1"), False),
            ("x" * 8000, False),
        ],
    )
    def test_request_id_is_echoed_only_when_safe(self, request_id, echoed):
        connection = http.client.HTTPConnection(API_HOST, API_PORT)
        connection.request("GET", "/pix/transaction/pix-inexistente", headers={"INTERNAL-TOKEN": INTERNAL_TOKEN, "X-Request-ID": request_id})
        response = connection.getresponse()
        returned = response.getheader("X-Request-ID")
        connection.close()

        assert response.status == 404
        assert returned
        assert (returned == request_id) is echoed
