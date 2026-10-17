"""Os testes deste pacote chamam o motor direto, sem HTTP e sem banco.

Eles precisam achar o pacote `src` (`controllers`, `models`...). Na
imagem de testes do Docker ele fica em /app; na sua máquina, em ../src.
"""
import sys
from os import environ
from pathlib import Path

SRC_CANDIDATES = [Path("/app"), Path(__file__).resolve().parents[2] / "src"]

for candidate in SRC_CANDIDATES:
    if (candidate / "controllers").is_dir():
        sys.path.insert(0, str(candidate))
        break

# Importar o motor importa o src/database.py, que monta o engine na
# carga do módulo. O engine não conecta até a primeira consulta, e estes
# testes não fazem nenhuma — mas ele precisa de um endereço para nascer.
environ.setdefault("DATABASE_URL", "postgresql+psycopg2://bootcamp:bootcamp@localhost:5432/bootcamp")
