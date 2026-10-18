from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, or_, text

from database import Context
from models import Analysis, AnalysisStatus, PixTransaction, TwoFactorResult

SAO_PAULO_TIMEZONE = "America/Sao_Paulo"


class PixTransactionRepository:
    """O histórico de tudo que chegou. Só aqui existe consulta sobre Pix.

    Nenhuma regra mora aqui: estas consultas devolvem NÚMEROS ("quantos
    destinos novos em 24 horas"), e quem decide se o número é suspeito é
    o src/controllers/risk_rules.py. Os limiares chegam por parâmetro
    justamente para que nenhum deles fique escondido numa consulta.
    """

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create(self, pix_data: dict) -> PixTransaction:
        pix_transaction = PixTransaction()

        for field, value in pix_data.items():
            setattr(pix_transaction, field, value)

        self.session.add(pix_transaction)
        return pix_transaction

    def get_by_pix_id(self, pix_id: str) -> Optional[PixTransaction]:
        return self.session.query(PixTransaction).filter(PixTransaction.pix_id == pix_id).first()

    def lock_payer(self, document: str) -> None:
        """Faz Pix simultâneos do MESMO pagador passarem um de cada vez.

        Sem a trava, dois Pix que chegam juntos leem o mesmo score (os
        pontos de um ainda não foram gravados quando o outro olha), e os
        dois passam onde o segundo deveria ter sido barrado.

        É uma trava consultiva (advisory) e não um SELECT ... FOR UPDATE
        porque não existe linha para travar: o antifraude não tem tabela
        de clientes. Ela vale até o fim da transação — o commit ou o
        rollback soltam sozinhos, e não há como esquecer de liberar.
        """
        self.session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:document))"), {"document": document})

    def payer_history(
        self,
        document: str,
        transaction_date: datetime,
        history_start: datetime,
        short_window_start: datetime,
        day_start: datetime,
        destination: dict,
        device_id: str,
        night_start_hour: int,
        night_end_hour: int,
        split_min_amount: int,
        split_max_amount: int,
    ) -> dict:
        """Tudo o que o motor precisa saber do pagador, numa consulta só.

        Uma consulta com FILTER, e não uma por regra: cada uma passaria
        pelas mesmas linhas do índice (source_document, transaction_date).

        Três recortes de status aparecem aqui, e a diferença entre eles
        é a parte que importa:

        • qualquer status — contar TENTATIVAS. Pix reprovado também é
          comportamento (fracionamento barrado continua sendo
          fracionamento).
        • aceito (approved ou 2FA não falhado) — somar DINHEIRO. Pix
          reprovado não saiu, e 2FA que falhou também não.
        • confiável (approved ou 2FA confirmado) — dizer que um destino é
          CONHECIDO. Só vira conhecido quem já recebeu de verdade.
        """
        accepted = and_(
            AnalysisStatus.enumerator.in_([AnalysisStatus.APPROVED, AnalysisStatus.TWO_FACTOR_REQUIRED]),
            or_(TwoFactorResult.result.is_(None), TwoFactorResult.result != TwoFactorResult.FAILED),
        )
        trusted = or_(
            AnalysisStatus.enumerator == AnalysisStatus.APPROVED,
            TwoFactorResult.result == TwoFactorResult.CONFIRMED,
        )
        same_destination = and_(
            PixTransaction.dest_participant == destination["participant"],
            PixTransaction.dest_branch == destination["branch"],
            PixTransaction.dest_account == destination["account"],
        )
        local_hour = func.extract("hour", func.timezone(SAO_PAULO_TIMEZONE, PixTransaction.transaction_date))
        at_night = or_(local_hour >= night_start_hour, local_hour < night_end_hour)
        in_short_window = PixTransaction.transaction_date > short_window_start

        row = (
            self.session.query(
                func.count(PixTransaction.id).label("pix_count"),
                func.count(PixTransaction.id).filter(accepted).label("accepted_count"),
                func.avg(PixTransaction.amount).filter(accepted).label("average_ticket"),
                func.coalesce(
                    func.sum(PixTransaction.amount).filter(and_(accepted, PixTransaction.transaction_date >= day_start)), 0
                ).label("sum_today"),
                func.coalesce(func.sum(PixTransaction.amount).filter(and_(accepted, in_short_window)), 0).label("sum_short_window"),
                func.count(PixTransaction.id)
                .filter(and_(in_short_window, at_night, PixTransaction.amount.between(split_min_amount, split_max_amount)))
                .label("split_count"),
                func.coalesce(func.bool_or(and_(same_destination, trusted)), False).label("known_destination"),
                func.coalesce(
                    func.bool_or(and_(in_short_window, PixTransaction.device_id != device_id)), False
                ).label("other_device_in_short_window"),
                func.count(PixTransaction.id)
                .filter(and_(in_short_window, TwoFactorResult.result == TwoFactorResult.FAILED))
                .label("failed_two_factor_count"),
            )
            .select_from(PixTransaction)
            .join(Analysis, Analysis.pix_transaction_id == PixTransaction.id)
            .join(AnalysisStatus, AnalysisStatus.id == Analysis.status_id)
            .outerjoin(TwoFactorResult, TwoFactorResult.analysis_id == Analysis.id)
            .filter(
                PixTransaction.source_document == document,
                PixTransaction.transaction_date > history_start,
                PixTransaction.transaction_date <= transaction_date,
            )
            .one()
        )

        average_ticket = None
        if row.average_ticket is not None:
            average_ticket = int(row.average_ticket)

        return {
            "pix_count": row.pix_count,
            "accepted_count": row.accepted_count,
            "average_ticket": average_ticket,
            "sum_today": int(row.sum_today),
            "sum_short_window": int(row.sum_short_window),
            "split_count": row.split_count,
            "known_destination": row.known_destination,
            "other_device_in_short_window": row.other_device_in_short_window,
            "failed_two_factor_count": row.failed_two_factor_count,
        }

    # As duas consultas abaixo estão em SQL escrito, e não no construtor
    # de consultas do SQLAlchemy: UNION e NOT EXISTS ficam mais legíveis
    # assim, e quem for conferir a regra lê a consulta como ela roda.

    def count_new_destinations(self, document: str, window_start: datetime, transaction_date: datetime, destination: dict) -> int:
        """Quantos destinos DIFERENTES, pagos na janela, o pagador nunca tinha pago antes dela.

        O destino deste Pix entra na conta (o UNION), porque a pergunta é
        "com este, quantos são?".
        """
        query = text(
            """
            SELECT COUNT(*) FROM (
                SELECT dest_participant, dest_branch, dest_account
                FROM pix_transaction
                WHERE source_document = :document
                  AND transaction_date > :window_start
                  AND transaction_date <= :transaction_date
                UNION
                SELECT CAST(:participant AS CHAR(8)), CAST(:branch AS VARCHAR(10)), CAST(:account AS VARCHAR(20))
            ) AS destination
            WHERE NOT EXISTS (
                SELECT 1 FROM pix_transaction AS earlier
                WHERE earlier.source_document = :document
                  AND earlier.dest_participant = destination.dest_participant
                  AND earlier.dest_branch = destination.dest_branch
                  AND earlier.dest_account = destination.dest_account
                  AND earlier.transaction_date <= :window_start
            )
            """
        )
        parameters = {
            "document": document,
            "window_start": window_start,
            "transaction_date": transaction_date,
            "participant": destination["participant"],
            "branch": destination["branch"],
            "account": destination["account"],
        }
        return self.session.execute(query, parameters).scalar()

    def count_destination_payers(self, destination: dict, window_start: datetime, transaction_date: datetime, document: str) -> int:
        """Quantos pagadores diferentes a conta de destino recebeu na janela, contando este."""
        query = text(
            """
            SELECT COUNT(*) FROM (
                SELECT source_document
                FROM pix_transaction
                WHERE dest_participant = :participant
                  AND dest_branch = :branch
                  AND dest_account = :account
                  AND transaction_date > :window_start
                  AND transaction_date <= :transaction_date
                UNION
                SELECT CAST(:document AS VARCHAR(14))
            ) AS payer
            """
        )
        parameters = {
            "participant": destination["participant"],
            "branch": destination["branch"],
            "account": destination["account"],
            "window_start": window_start,
            "transaction_date": transaction_date,
            "document": document,
        }
        return self.session.execute(query, parameters).scalar()

    def last_device_id(self, document: str, transaction_date: datetime) -> Optional[str]:
        """O aparelho do Pix mais recente do pagador antes deste. None se ele nunca fez Pix."""
        return (
            self.session.query(PixTransaction.device_id)
            .filter(
                PixTransaction.source_document == document,
                PixTransaction.transaction_date <= transaction_date,
            )
            .order_by(PixTransaction.transaction_date.desc(), PixTransaction.id.desc())
            .limit(1)
            .scalar()
        )

    def count_other_payers_on_device(
        self, device_id: str, window_start: datetime, transaction_date: datetime, document: str
    ) -> int:
        """Quantos OUTROS pagadores usaram este mesmo aparelho na janela."""
        return (
            self.session.query(func.count(func.distinct(PixTransaction.source_document)))
            .filter(
                PixTransaction.device_id == device_id,
                PixTransaction.source_document != document,
                PixTransaction.transaction_date > window_start,
                PixTransaction.transaction_date <= transaction_date,
            )
            .scalar()
        )
