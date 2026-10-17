from utils.document_number import is_person, is_valid_document, only_digits


class TestDocumentNumber:
    def test_cpf_with_and_without_mask_is_the_same(self):
        assert only_digits("123.456.789-09") == "12345678909"
        assert is_valid_document("123.456.789-09")
        assert is_valid_document("12345678909")

    def test_wrong_check_digits_and_repeated_digits_are_invalid(self):
        assert not is_valid_document("111.222.333-44")
        assert not is_valid_document("111.111.111-11")

    def test_cnpj(self):
        # CNPJ de teste conhecido, válido na conta dos dígitos
        assert is_valid_document("11.222.333/0001-81")
        assert not is_valid_document("11.222.333/0001-82")
        assert not is_person("11.222.333/0001-81")
        assert is_person("123.456.789-09")
