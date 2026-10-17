from copy import deepcopy

from models import Analysis
from utils.clock import to_local_isoformat


class AnalysisDTO:
    """Traduz a análise gravada no JSON da resposta: { key, status, metadata }.

    É o contrato do desafio, e o POST e o GET devolvem a mesma forma.
    """

    @staticmethod
    def obj_to_dict(analysis: Analysis) -> dict:
        # O deepcopy não é frescura: sem ele, o `two_factor` abaixo seria
        # escrito dentro do JSON que vive no objeto do banco — e a análise
        # é imutável.
        metadata = deepcopy(analysis.analysis_metadata)

        # O resultado do 2FA não faz parte da decisão: aconteceu depois,
        # e mora em outra tabela. Ele aparece aqui, ao lado, para quem
        # consulta ver as duas coisas de uma vez.
        two_factor_result = analysis.two_factor_result
        if two_factor_result is not None:
            metadata["two_factor"] = {
                "result": two_factor_result.result,
                "answered_at": to_local_isoformat(two_factor_result.created_at),
            }

        return {
            "key": analysis.analysis_key,
            "status": analysis.status.enumerator,
            "metadata": metadata,
        }
