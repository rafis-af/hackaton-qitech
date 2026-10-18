CPF_LENGTH = 11
CNPJ_LENGTH = 14

CHECK_DIGIT_POSITIONS = [9, 10]

CNPJ_FIRST_WEIGHTS = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
CNPJ_SECOND_WEIGHTS = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def only_digits(document_number: str) -> str:
    digits = []
    for character in document_number:
        if character.isdigit():
            digits.append(character)

    return "".join(digits)


def is_person(document_number: str) -> bool:
    return len(only_digits(document_number)) == CPF_LENGTH


def is_valid_document(document_number: str) -> bool:
    digits = only_digits(document_number)

    if len(digits) == CPF_LENGTH:
        return is_valid_cpf(digits)

    if len(digits) == CNPJ_LENGTH:
        return is_valid_cnpj(digits)

    return False


def is_valid_cpf(document_number: str) -> bool:
    digits = [int(character) for character in only_digits(document_number)]

    if len(digits) != CPF_LENGTH:
        return False

    if _all_digits_are_equal(digits):
        return False

    for position in CHECK_DIGIT_POSITIONS:
        total = 0
        first_weight = position + 1

        for index in range(position):
            total = total + digits[index] * (first_weight - index)

        if digits[position] != _check_digit(total):
            return False

    return True


def is_valid_cnpj(document_number: str) -> bool:
    digits = [int(character) for character in only_digits(document_number)]

    if len(digits) != CNPJ_LENGTH:
        return False

    if _all_digits_are_equal(digits):
        return False

    for weights in [CNPJ_FIRST_WEIGHTS, CNPJ_SECOND_WEIGHTS]:
        position = len(weights)
        total = 0

        for index, weight in enumerate(weights):
            total = total + digits[index] * weight

        if digits[position] != _check_digit(total):
            return False

    return True


def _check_digit(total: int) -> int:
    remainder = total % 11

    if remainder < 2:
        return 0

    return 11 - remainder


def _all_digits_are_equal(digits: list) -> bool:
    for digit in digits:
        if digit != digits[0]:
            return False

    return True
