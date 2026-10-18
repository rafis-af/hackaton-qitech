import os


SERVICE_ROOT = os.path.abspath(os.path.dirname(__file__))

# Onde moram os arquivos de schema — os .json que descrevem o formato
# que cada requisicao precisa ter. Quem le essa pasta e o
# src/utils/schema_handler.py.
SCHEMA_PATH = os.path.join(SERVICE_ROOT, "schemas")

APP_ENV = os.environ.get("APP_ENV", "local")
SERVICE_NAME = os.environ.get("SERVICE_NAME", "antifraude-pix-api")

DATABASE_URL = os.environ.get("DATABASE_URL")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN")

# A Receita Federal: o único serviço de fora que o antifraude consulta,
# sobre quem RECEBE o Pix. Na sua máquina, quem responde é o mock em
# mocks/receita/ (sobe junto no docker compose).
#
# RECEITA_ENABLED=false desliga a consulta inteira: o motor roda igual,
# só sem as duas regras da Receita. É o que deixa o projeto de pé mesmo
# sem o container do mock.
RECEITA_ENABLED = os.environ.get("RECEITA_ENABLED", "true").lower() == "true"
RECEITA_API_URL = os.environ.get("RECEITA_API_URL", "http://localhost:8080")
RECEITA_API_INTERNAL_TOKEN = os.environ.get("RECEITA_API_INTERNAL_TOKEN", "default_token")

# Segundos. Curto de propósito: o cliente está esperando o Pix sair, e a
# decisão não pode ficar pendurada na rede. Passou disso, o motor segue
# sem a resposta da Receita.
RECEITA_API_TIMEOUT = float(os.environ.get("RECEITA_API_TIMEOUT", "0.5"))

# Quanto tempo uma resposta da Receita vale no cache (document_situation).
# Situação cadastral muda raramente; um dia evita consultar a Receita a
# cada Pix para o mesmo destino.
RECEITA_CACHE_HOURS = int(os.environ.get("RECEITA_CACHE_HOURS", "24"))

# Rotas públicas: não exigem o header INTERNAL-TOKEN. São as duas que
# precisam responder pra quem ainda não tem token nenhum: a raiz, que
# diz quem é este serviço, e o health check, que o Docker consulta pra
# saber se a API já está de pé.
BYPASS_ENDPOINTS = [
    "/",
    "/health_check",
]

REQUIRED_VARIABLES = ["DATABASE_URL", "INTERNAL_TOKEN"]

# Os ambientes em que os valores de exemplo (token "default_token", banco
# com senha "bootcamp") são aceitos: a sua máquina e os testes. Em
# qualquer outro, a API se recusa a subir com eles.
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
    """Quais configurações ainda estão com o valor de exemplo, num ambiente que não é de desenvolvimento.

    O token e a senha do banco do docker-compose.yml estão escritos no
    repositório: qualquer pessoa que o leia os conhece. Na sua máquina,
    tudo bem — é o que deixa o projeto subir sem configurar nada. Em
    produção, seria uma porta aberta. É melhor a API falhar na hora de
    ligar, com uma mensagem clara, do que subir e atender qualquer um.
    """
    if app_env.lower() in DEVELOPMENT_ENVIRONMENTS:
        return []

    insecure = []

    for name, value in settings.items():
        if value == EXAMPLE_TOKEN or (value and EXAMPLE_DATABASE_PASSWORD in value):
            insecure.append(name)

    return insecure
