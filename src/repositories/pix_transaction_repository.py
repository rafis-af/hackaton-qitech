from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import and_, func, or_, text

from database import Context
from models import Analysis, AnalysisStatus, PixTransaction, TwoFactorResult

SAO_PAULO_TIMEZONE = "America/Sao_Paulo"


class PixTransactionRepository:
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
