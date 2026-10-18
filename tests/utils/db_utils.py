from os import environ
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError


RESET_QUERIES = [
    "DROP SCHEMA public CASCADE;",
    "CREATE SCHEMA public;",
    "GRANT ALL ON SCHEMA public TO CURRENT_USER;",
    "GRANT ALL ON SCHEMA public TO public;",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILE = PROJECT_ROOT / "database" / "database.sql"

DEFAULT_DATABASE_URL = "postgresql+psycopg2://bootcamp:bootcamp@localhost:5432/bootcamp"

DATABASE_OFFLINE = (
    "Não consegui falar com o banco em {host}:{port}.\n"
    "Ele precisa estar de pé pros testes rodarem. Suba com:  docker compose up\n"
    "Se ele já está de pé, confira a porta na DATABASE_URL do seu .env."
)


class DbUtils:
    @staticmethod
    def database_url() -> str:
        return environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

    @staticmethod
    def rollback() -> None:
        engine = create_engine(DbUtils.database_url(), isolation_level="AUTOCOMMIT")

        try:
            with engine.connect() as connection:
                for query in RESET_QUERIES:
                    connection.exec_driver_sql(query)

                with connection.connection.cursor() as cursor:
                    cursor.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        except OperationalError:
            message = DATABASE_OFFLINE.format(host=engine.url.host, port=engine.url.port)
            raise RuntimeError(message) from None
        finally:
            engine.dispose()

    @staticmethod
    def expire_two_factor(pix_id: str) -> None:
        engine = create_engine(DbUtils.database_url(), isolation_level="AUTOCOMMIT")

        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    """
                    UPDATE two_factor_result
                    SET created_at = NOW() - INTERVAL '1 hour',
                        expires_at = NOW() - INTERVAL '1 minute'
                    WHERE analysis_id = (
                        SELECT analysis.id
                        FROM analysis
                        JOIN pix_transaction ON pix_transaction.id = analysis.pix_transaction_id
                        WHERE pix_transaction.pix_id = %(pix_id)s
                    )
                    """,
                    {"pix_id": pix_id},
                )
        finally:
            engine.dispose()
