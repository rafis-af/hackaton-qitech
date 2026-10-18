from copy import deepcopy

from models import Analysis
from utils.clock import to_local_isoformat


class AnalysisDTO:
    @staticmethod
    def obj_to_dict(analysis: Analysis) -> dict:
        metadata = deepcopy(analysis.analysis_metadata)

        two_factor_result = analysis.two_factor_result
        if two_factor_result is not None:
            resolved_at = None
            if two_factor_result.resolved_at is not None:
                resolved_at = to_local_isoformat(two_factor_result.resolved_at)

            metadata["two_factor"] = {
                "result": two_factor_result.result,
                "expires_at": to_local_isoformat(two_factor_result.expires_at),
                "resolved_at": resolved_at,
            }

        return {
            "key": analysis.analysis_key,
            "status": analysis.status.enumerator,
            "metadata": metadata,
        }
