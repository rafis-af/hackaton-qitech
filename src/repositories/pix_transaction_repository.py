from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import and_, func, or_, text

from database import Context
from models import Analysis, AnalysisStatus, PixTransaction, TwoFactorResult

SAO_PAULO_TIMEZONE = "America/Sao_Paulo"


class PixTransactionRepository:
    """O histórico de tudo que chegou. Só aqui existe consulta sobre Pix.

    Nenhuma regra mora aqui: estas consultas devolvem NÚMEROS ("quantos Pix
    o cliente fez nesta faixa de horário"), e quem decide se o número é
    suspeito é o src/controllers/risk_rules.py. As janelas e as faixas
    chegam por parâmetro justamente para que nenhum limiar fique escondido
    numa consulta.
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

        Sem a trava, dois Pix que chegam juntos leem o mesmo histórico (o
        primeiro ainda não foi gravado quando o segundo olha), e o segundo
        não herda o risco do primeiro nem conta na velocidade.

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
        burst_window_start: datetime,
        destination: dict,
        device_id: str,
        hour_band: List[int],
    ) -> dict:
        """O "normal" do pagador, numa consulta só.

        Uma consulta com FILTER, e não uma por métrica: cada uma passaria
        pelas mesmas linhas do índice (source_document, transaction_date).

        Dois recortes aparecem aqui, e a diferença entre eles é a parte
        que importa:

        • confiável (approved, ou 2FA confirmado) — dizer o que é NORMAL
          para o cliente: o horário de costume, o ticket médio, o destino
          conhecido. O que o ladrão fez (reprovado, 2FA pendente ou
          falhado) não pode virar o normal de ninguém.
        • qualquer status — contar TENTATIVAS. A rajada do ladrão conta
          mesmo quando foi barrada.
        """
        trusted = self._trusted()
        same_destination = and_(
            PixTransaction.dest_participant == destination["participant"],
            PixTransaction.dest_branch == destination["branch"],
            PixTransaction.dest_account == destination["account"],
        )
        local_hour = func.extract("hour", func.timezone(SAO_PAULO_TIMEZONE, PixTransaction.transaction_date))
        in_short_window = PixTransaction.transaction_date > short_window_start

        row = (
            self.session.query(
                func.count(PixTransaction.id).filter(trusted).label("trusted_count"),
                func.avg(PixTransaction.amount).filter(trusted).label("average_ticket"),
                func.count(PixTransaction.id).filter(and_(trusted, local_hour.in_(hour_band))).label("same_hour_band_count"),
                func.coalesce(func.bool_or(and_(same_destination, trusted)), False).label("known_destination"),
                func.count(PixTransaction.id)
                .filter(PixTransaction.transaction_date > burst_window_start)
                .label("burst_count"),
                func.coalesce(
                    func.bool_or(and_(in_short_window, PixTransaction.device_id != device_id)), False
                ).label("other_device_in_short_window"),
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
            "trusted_count": row.trusted_count,
            "average_ticket": average_ticket,
            "same_hour_band_count": row.same_hour_band_count,
            "known_destination": row.known_destination,
            "burst_count": row.burst_count,
            "other_device_in_short_window": row.other_device_in_short_window,
        }

    def known_places(self, document: str, history_start: datetime, transaction_date: datetime) -> List[Tuple[float, float]]:
        """Os lugares de costume do pagador: de onde saíram os Pix confiáveis dele na janela.

        Só Pix aprovado, ou com 2FA confirmado, ensina um lugar. Um Pix
        reprovado, ou com 2FA pendente ou falhado, pode ter sido feito
        pelo ladrão — e o lugar do ladrão não pode virar "de costume".

        As coordenadas saem arredondadas em duas casas (~1 km), sem
        repetição: cem Pix feitos de casa viram um lugar só, e a conta de
        distância do Modo Rua roda sobre poucos pontos.
        """
        rounded_latitude = func.round(PixTransaction.latitude, 2)
        rounded_longitude = func.round(PixTransaction.longitude, 2)

        rows = (
            self.session.query(rounded_latitude, rounded_longitude)
            .select_from(PixTransaction)
            .join(Analysis, Analysis.pix_transaction_id == PixTransaction.id)
            .join(AnalysisStatus, AnalysisStatus.id == Analysis.status_id)
            .outerjoin(TwoFactorResult, TwoFactorResult.analysis_id == Analysis.id)
            .filter(
                PixTransaction.source_document == document,
                PixTransaction.transaction_date > history_start,
                PixTransaction.transaction_date <= transaction_date,
                PixTransaction.latitude.isnot(None),
                self._trusted(),
            )
            .distinct()
            .all()
        )

        return [(float(latitude), float(longitude)) for latitude, longitude in rows]

    def count_destination_payers(self, destination: dict, window_start: datetime, transaction_date: datetime, document: str) -> int:
        """Quantos pagadores diferentes a conta de destino recebeu na janela, contando este.

        Em SQL escrito, e não no construtor de consultas: o UNION fica mais
        legível assim, e quem for conferir a regra lê a consulta como ela
        roda.
        """
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

    def count_other_payers_on_ip(self, ip: str, window_start: datetime, transaction_date: datetime, document: str) -> int:
        """Quantos OUTROS pagadores usaram este mesmo IP na janela."""
        return (
            self.session.query(func.count(func.distinct(PixTransaction.source_document)))
            .filter(
                PixTransaction.ip == ip,
                PixTransaction.source_document != document,
                PixTransaction.transaction_date > window_start,
                PixTransaction.transaction_date <= transaction_date,
            )
            .scalar()
        )

    def _trusted(self):
        return or_(
            AnalysisStatus.enumerator == AnalysisStatus.APPROVED,
            TwoFactorResult.result == TwoFactorResult.CONFIRMED,
        )
