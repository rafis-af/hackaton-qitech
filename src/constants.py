import os


SERVICE_ROOT = os.path.abspath(os.path.dirname(__file__))

SCHEMA_PATH = os.path.join(SERVICE_ROOT, "schemas")

APP_ENV = os.environ.get("APP_ENV", "local")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "antifraude-pix-api")

DATABASE_URL = os.environ.get("DATABASE_URL")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN")

RECEITA_ENABLED = os.environ.get("RECEITA_ENABLED", "true").lower() == "true"
RECEITA_API_URL = os.environ.get("RECEITA_API_URL", "http://localhost:8080")
RECEITA_API_INTERNAL_TOKEN = os.environ.get("RECEITA_API_INTERNAL_TOKEN", "default_token")

RECEITA_API_TIMEOUT = float(os.environ.get("RECEITA_API_TIMEOUT", "0.5"))

RECEITA_CACHE_HOURS = int(os.environ.get("RECEITA_CACHE_HOURS", "24"))

BYPASS_ENDPOINTS = [
    "/",
    "/health_check",
]

REQUIRED_VARIABLES = ["DATABASE_URL", "INTERNAL_TOKEN"]

DEVELOPMENT_ENVIRONMENTS = ["local", "test"]
EXAMPLE_TOKEN = "default_token"
EXAMPLE_DATABASE_PASSWORD = ":bootcamp@"


def check_variables():
    missing = []
    for name in REQUIRED_VARIABLES:
        if not globals().get(name):
            missing.append(name)

    if missing:
        raise EnvironmentError(
            f"Faltam variáveis de ambiente: {', '.join(missing)}. "
            "Rodando com 'docker compose up' elas já vêm preenchidas. "
            "Fora do Docker, copie o .env.example para .env."
        )

    insecure = find_example_secrets(
        APP_ENV,
        {
            "INTERNAL_TOKEN": INTERNAL_TOKEN,
            "RECEITA_API_INTERNAL_TOKEN": RECEITA_API_INTERNAL_TOKEN,
            "DATABASE_URL": DATABASE_URL,
        },
    )

    if insecure:
        raise EnvironmentError(
            f"APP_ENV={APP_ENV} com valores de exemplo em: {', '.join(insecure)}. "
            "Eles servem para estudar na sua máquina e são públicos (estão no "
            "repositório). Fora de local/test, defina valores próprios."
        )


def find_example_secrets(app_env: str, settings: dict) -> list:
    if app_env.lower() in DEVELOPMENT_ENVIRONMENTS:
        return []

    insecure = []

    for name, value in settings.items():
        if value == EXAMPLE_TOKEN or (value and EXAMPLE_DATABASE_PASSWORD in value):
            insecure.append(name)

    return insecure
