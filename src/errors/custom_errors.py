from errors import QIException


class PixTransactionConflict(QIException):
    """O mesmo `id` de Pix chegou de novo, com outros dados.

    O reenvio com os MESMOS dados não é erro: devolve a análise original
    com 200. Já um `id` repetido com valor, conta ou data diferentes é
    outro Pix tentando usar um identificador que já tem dono — e
    analisá-lo por cima apagaria a decisão anterior.
    """

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
    """Só existe resultado de 2FA para Pix em que o motor PEDIU 2FA."""

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
    """A resposta do 2FA chegou depois do prazo.

    409, e não 400: o pedido está bem escrito (400 é "não consegui ler o
    seu pedido", como diz o Base Service). O que impede é o estado da
    pendência, que já fechou — a mesma família dos outros dois erros de
    2FA aqui em cima.
    """

    code = "QIT002006"

    def __init__(self, pix_id, expires_at) -> None:
        title = "Two factor expired"
        http_status = 409
        description = f"The two factor of Pix {pix_id} expired at {expires_at}."
        translation = f"O prazo do 2FA do Pix {pix_id} acabou em {expires_at}: o Pix não pode mais ser confirmado."
        super().__init__(title, self.code, http_status, description, translation)


class InvalidDateTime(QIException):
    """A data tem o formato certo e não existe no calendário.

    422, e não 400: a API leu o pedido e entendeu, e o valor é que não
    pode existir.
    """

    code = "QIT002005"

    def __init__(self, field, raw_datetime) -> None:
        title = "Invalid Date"
        http_status = 422
        description = f"The value {raw_datetime} in {field} is not a real date."
        translation = f"A data {raw_datetime} em {field} não existe."
        super().__init__(title, self.code, http_status, description, translation)
