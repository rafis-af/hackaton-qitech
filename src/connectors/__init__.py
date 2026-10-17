# O antifraude não chama nenhum serviço de fora: a decisão do Pix não
# pode depender da rede (o desafio deixa "Outras APIs" fora do escopo).
# A base do Base Service fica aqui para o dia em que um connector entrar.
from connectors.rest_connector import RestConnector
