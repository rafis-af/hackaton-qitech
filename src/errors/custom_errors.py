from errors import QIException


class PixTransactionConflict(QIException):
    code = "QIT002001"

    def __init__(self, pix_id) -> None:
        title = "Pix id already used"
        http_status = 409
        description = f"The Pix {pix_id} was already received with different data."
        translation = f"O Pix {pix_id} já foi recebido com outros dados."
        super().__init__(title, self.code, http_status, description, translation)


class PixTransactionNotFound(QIException):
    code = "QIT002002"

    def __init__(self, pix_id) -> None:
        title = "Pix not Found"
        http_status = 404
        description = f"The Pix {pix_id} was not found."
        translation = f"O Pix {pix_id} não foi encontrado."
        super().__init__(title, self.code, http_status, description, translation)


class TwoFactorNotRequired(QIException):
    code = "QIT002003"

    def __init__(self, pix_id, status) -> None:
        title = "Two factor was not required"
        http_status = 409
        description = f"The Pix {pix_id} was {status}, so there is no two factor to answer."
        translation = f"O Pix {pix_id} foi {status}: não há 2FA para responder."
        super().__init__(title, self.code, http_status, description, translation)


class TwoFactorAlreadyAnswered(QIException):
    code = "QIT002004"

    def __init__(self, pix_id) -> None:
        title = "Two factor already answered"
        http_status = 409
        description = f"The two factor of Pix {pix_id} was already answered."
        translation = f"O 2FA do Pix {pix_id} já foi respondido."
        super().__init__(title, self.code, http_status, description, translation)


class TwoFactorExpired(QIException):
    code = "QIT002006"

    def __init__(self, pix_id, expires_at) -> None:
        title = "Two factor expired"
        http_status = 409
        description = f"The two factor of Pix {pix_id} expired at {expires_at}."
        translation = f"O prazo do 2FA do Pix {pix_id} acabou em {expires_at}: o Pix não pode mais ser confirmado."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidDateTime(QIException):
    code = "QIT002005"

    def __init__(self, field, raw_datetime) -> None:
        title = "Invalid Date"
        http_status = 422
        description = f"The value {raw_datetime} in {field} is not a real date."
        translation = f"A data {raw_datetime} em {field} não existe."
        super().__init__(title, self.code, http_status, description, translation)
