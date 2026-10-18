import math
from datetime import timedelta
from typing import Callable, Dict, List, Optional

from models import Analysis, AnalysisStatus, DocumentSituation, Rule, TwoFactorResult


MAX_RISK = 100
TWO_FACTOR_FROM = 40
REJECT_FROM = 70

HISTORY_DAYS = 90

HOUR_BAND_WIDTH = 1
MIN_HOUR_HISTORY = 5
RARE_HOUR_SHARE = 0.10
NIGHT_START_HOUR = 20
NIGHT_END_HOUR = 6

MIN_TICKET_HISTORY = 3
AMOUNT_RATIO_HIGH = 5
AMOUNT_RATIO_MEDIUM = 3
AMOUNT_RATIO_LOW = 2
HIGH_AMOUNT_WITHOUT_HISTORY = 100000

STREET_MODE_RADIUS_KM = 30
EARTH_RADIUS_KM = 6371

BURST_WINDOW_MINUTES = 10
BURST_MEDIUM = 3
BURST_HIGH = 4

NEW_ACCOUNT_DAYS = 30
DESTINATION_PAYERS_THRESHOLD = 5
DESTINATION_PAYERS_WINDOW_HOURS = 24

RECEITA_BLOCKED_SITUATIONS = [
    DocumentSituation.NOT_FOUND,
    DocumentSituation.CANCELED,
    DocumentSituation.NULLIFIED,
    DocumentSituation.DECEASED,
]
RECEITA_IRREGULAR_SITUATIONS = [
    DocumentSituation.SUSPENDED,
    DocumentSituation.PENDING_REGULARIZATION,
]

DEVICE_CHANGE_WINDOW_HOURS = 24
DEVICE_SHARED_WINDOW_DAYS = 30
IP_SHARED_WINDOW_HOURS = 24

MEMORY_HOURS = 24
CARRY_WHEN_CONFIRMED = 0.0
CARRY_WHEN_FAILED = 1.0
CARRY_OTHERWISE = 0.5


def check_invalid_document_number(facts: dict) -> Optional[dict]:
    invalid_fields = []

    if not facts["payer"]["document_is_valid"]:
        invalid_fields.append("source_account.owner.document_number")

    if not facts["destination"]["document_is_valid"]:
        invalid_fields.append("destination_account.owner.document_number")

    if not invalid_fields:
        return None

    return {"invalid_fields": invalid_fields}


def check_restricted_payer(facts: dict) -> Optional[dict]:
    reason = facts["payer"]["restricted_reason"]

    if reason is None:
        return None

    return {"reason": reason}


def check_restricted_destination(facts: dict) -> Optional[dict]:
    reason = facts["destination"]["restricted_reason"]

    if reason is None:
        return None

    return {"reason": reason}


def check_receita_destination_blocked(facts: dict) -> Optional[dict]:
    situation = facts["destination"]["receita_situation"]

    if situation not in RECEITA_BLOCKED_SITUATIONS:
        return None

    return {"situation": situation}


def check_unusual_hour(facts: dict) -> Optional[dict]:
    history = facts["history"]

    if history["trusted_count"] < MIN_HOUR_HISTORY or history["same_hour_band_count"] > 0:
        return None

    return _hour_observed(facts)


def check_rare_hour(facts: dict) -> Optional[dict]:
    history = facts["history"]

    if history["trusted_count"] < MIN_HOUR_HISTORY or history["same_hour_band_count"] == 0:
        return None

    if _hour_share(history) >= RARE_HOUR_SHARE:
        return None

    return _hour_observed(facts)


def check_night_without_history(facts: dict) -> Optional[dict]:
    if facts["history"]["trusted_count"] >= MIN_HOUR_HISTORY:
        return None

    if not is_night(facts["transaction_date"].hour):
        return None

    return {"local_time": facts["transaction_date"].strftime("%H:%M"), "night": f"{NIGHT_START_HOUR}h às {NIGHT_END_HOUR}h"}


def check_amount_5x_profile(facts: dict) -> Optional[dict]:
    return _amount_ratio_between(facts, AMOUNT_RATIO_HIGH, None)


def check_amount_3x_profile(facts: dict) -> Optional[dict]:
    return _amount_ratio_between(facts, AMOUNT_RATIO_MEDIUM, AMOUNT_RATIO_HIGH)


def check_amount_2x_profile(facts: dict) -> Optional[dict]:
    return _amount_ratio_between(facts, AMOUNT_RATIO_LOW, AMOUNT_RATIO_MEDIUM)


def check_high_amount_without_history(facts: dict) -> Optional[dict]:
    if facts["history"]["trusted_count"] >= MIN_TICKET_HISTORY:
        return None

    if facts["amount"] <= HIGH_AMOUNT_WITHOUT_HISTORY:
        return None

    return {"amount": facts["amount"], "threshold": HIGH_AMOUNT_WITHOUT_HISTORY}


def check_far_from_usual_places(facts: dict) -> Optional[dict]:
    location = facts["location"]
    known_places = facts["known_places"]

    if location is None or not known_places:
        return None

    nearest_km = min(distance_km(location, place) for place in known_places)

    if nearest_km <= STREET_MODE_RADIUS_KM:
        return None

    return {
        "nearest_known_place_km": round(nearest_km, 1),
        "radius_km": STREET_MODE_RADIUS_KM,
        "known_places": len(known_places),
    }


def check_burst_4_in_10_minutes(facts: dict) -> Optional[dict]:
    pix_count = _burst_count(facts)

    if pix_count < BURST_HIGH:
        return None

    return {"pix_count": pix_count, "window_minutes": BURST_WINDOW_MINUTES}


def check_burst_3_in_10_minutes(facts: dict) -> Optional[dict]:
    pix_count = _burst_count(facts)

    if pix_count != BURST_MEDIUM:
        return None

    return {"pix_count": pix_count, "window_minutes": BURST_WINDOW_MINUTES}


def check_new_destination(facts: dict) -> Optional[dict]:
    if facts["history"]["known_destination"]:
        return None

    return {"known_destination": False}


def check_new_destination_account(facts: dict) -> Optional[dict]:
    account_age = facts["transaction_date"] - facts["destination"]["opening_date"]

    if account_age >= timedelta(days=NEW_ACCOUNT_DAYS):
        return None

    return {"account_age_days": account_age.days, "threshold_days": NEW_ACCOUNT_DAYS}


def check_destination_many_payers(facts: dict) -> Optional[dict]:
    payers = facts["destination_payers"]

    if payers < DESTINATION_PAYERS_THRESHOLD:
        return None

    return {"payers": payers, "threshold": DESTINATION_PAYERS_THRESHOLD, "window_hours": DESTINATION_PAYERS_WINDOW_HOURS}


def check_receita_destination_irregular(facts: dict) -> Optional[dict]:
    situation = facts["destination"]["receita_situation"]

    if situation not in RECEITA_IRREGULAR_SITUATIONS:
        return None

    return {"situation": situation}


def check_device_changed(facts: dict) -> Optional[dict]:
    last_device_id = facts["last_device_id"]

    changed_now = last_device_id is not None and last_device_id != facts["device_id"]
    changed_recently = facts["history"]["other_device_in_short_window"]

    if not changed_now and not changed_recently:
        return None

    return {"changed_in_this_pix": changed_now, "window_hours": DEVICE_CHANGE_WINDOW_HOURS}


def check_device_shared(facts: dict) -> Optional[dict]:
    other_payers = facts["other_payers_on_device"]

    if other_payers == 0:
        return None

    return {"other_payers": other_payers, "window_days": DEVICE_SHARED_WINDOW_DAYS}


def check_ip_shared(facts: dict) -> Optional[dict]:
    other_payers = facts["other_payers_on_ip"]

    if other_payers == 0:
        return None

    return {"other_payers": other_payers, "window_hours": IP_SHARED_WINDOW_HOURS}


RULE_CHECKS: Dict[str, Callable[[dict], Optional[dict]]] = {
    "invalid_document_number": check_invalid_document_number,
    "restricted_payer": check_restricted_payer,
    "restricted_destination": check_restricted_destination,
    "receita_destination_blocked": check_receita_destination_blocked,
    "unusual_hour": check_unusual_hour,
    "rare_hour": check_rare_hour,
    "night_without_history": check_night_without_history,
    "amount_5x_profile": check_amount_5x_profile,
    "amount_3x_profile": check_amount_3x_profile,
    "amount_2x_profile": check_amount_2x_profile,
    "high_amount_without_history": check_high_amount_without_history,
    "far_from_usual_places": check_far_from_usual_places,
    "burst_4_in_10_minutes": check_burst_4_in_10_minutes,
    "burst_3_in_10_minutes": check_burst_3_in_10_minutes,
    "new_destination": check_new_destination,
    "new_destination_account": check_new_destination_account,
    "destination_many_payers": check_destination_many_payers,
    "receita_destination_irregular": check_receita_destination_irregular,
    "device_changed": check_device_changed,
    "device_shared": check_device_shared,
    "ip_shared": check_ip_shared,
}


def evaluate(rules: list, facts: dict) -> List[dict]:
    signals = []

    for rule in rules:
        check = RULE_CHECKS.get(rule.enumerator)

        if check is None:
            continue

        observed = check(facts)

        if observed is None:
            continue

        metric = None
        if rule.risk_metric is not None:
            metric = rule.risk_metric.enumerator

        signals.append(
            {
                "rule": rule.enumerator,
                "metric": metric,
                "action": rule.action,
                "points": rule.points,
                "description": rule.description,
                "observed": observed,
            }
        )

    return signals


def score_metrics(signals: List[dict], metrics: list) -> List[dict]:
    scored = []

    for metric in metrics:
        metric_signals = [signal for signal in signals if signal["metric"] == metric.enumerator]
        raw_points = sum(signal["points"] for signal in metric_signals)

        scored.append(
            {
                "metric": metric.enumerator,
                "label": metric.label,
                "points": min(raw_points, metric.max_points),
                "max_points": metric.max_points,
                "rules": [signal["rule"] for signal in metric_signals],
            }
        )

    return scored


def inherit_risk(previous_analyses: List[dict]) -> dict:
    inherited = {"points": 0, "from_pix_id": None, "carried_fraction": None}

    for previous in previous_analyses:
        fraction = carried_fraction(previous["two_factor_result"])
        points = int(previous["transaction_risk"] * fraction)

        if points > inherited["points"]:
            inherited = {"points": points, "from_pix_id": previous["pix_id"], "carried_fraction": fraction}

    return inherited


def carried_fraction(two_factor_result: Optional[str]) -> float:
    if two_factor_result == TwoFactorResult.CONFIRMED:
        return CARRY_WHEN_CONFIRMED

    if two_factor_result == TwoFactorResult.FAILED:
        return CARRY_WHEN_FAILED

    return CARRY_OTHERWISE


def decide(signals: List[dict], metric_scores: List[dict], inherited_points: int) -> dict:
    transaction_risk = min(MAX_RISK, sum(metric["points"] for metric in metric_scores))
    risk_score = min(MAX_RISK, transaction_risk + inherited_points)

    reject_signals = [signal for signal in signals if signal["action"] == Rule.REJECT]
    score_signals = [signal for signal in signals if signal["action"] == Rule.SCORE]

    risk_summary = (
        f"risco {risk_score} de {MAX_RISK} ({transaction_risk} deste Pix + {inherited_points} herdado "
        f"das últimas {MEMORY_HOURS}h)" + _metrics_summary(metric_scores)
    )

    if reject_signals:
        status = AnalysisStatus.REJECTED
        reason = Analysis.BLOCKING_RULE
        decisive_rules = [signal["rule"] for signal in reject_signals]
        description = "Reprovado por um fato: " + "; ".join(signal["description"] for signal in reject_signals)
    elif risk_score >= REJECT_FROM:
        status = AnalysisStatus.REJECTED
        reason = Analysis.RISK_SCORE
        decisive_rules = [signal["rule"] for signal in score_signals]
        description = f"Reprovado: {risk_summary}"
    elif risk_score >= TWO_FACTOR_FROM:
        status = AnalysisStatus.TWO_FACTOR_REQUIRED
        reason = Analysis.RISK_SCORE
        decisive_rules = [signal["rule"] for signal in score_signals]
        description = f"2FA pedido: {risk_summary}"
    else:
        status = AnalysisStatus.APPROVED
        reason = Analysis.RISK_SCORE
        decisive_rules = []
        description = f"Aprovado: {risk_summary}"

    return {
        "status": status,
        "reason": reason,
        "decisive_rules": decisive_rules,
        "description": description,
        "transaction_risk": transaction_risk,
        "inherited_risk": inherited_points,
        "risk_score": risk_score,
    }


def is_night(hour: int) -> bool:
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def hour_band(hour: int) -> List[int]:
    return sorted({(hour + offset) % 24 for offset in range(-HOUR_BAND_WIDTH, HOUR_BAND_WIDTH + 1)})


def distance_km(origin: tuple, destination: tuple) -> float:
    latitude_1, longitude_1 = (math.radians(value) for value in origin)
    latitude_2, longitude_2 = (math.radians(value) for value in destination)

    half_chord = (
        math.sin((latitude_2 - latitude_1) / 2) ** 2
        + math.cos(latitude_1) * math.cos(latitude_2) * math.sin((longitude_2 - longitude_1) / 2) ** 2
    )

    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(half_chord))


def _hour_share(history: dict) -> float:
    return history["same_hour_band_count"] / history["trusted_count"]


def _hour_observed(facts: dict) -> dict:
    history = facts["history"]
    hour = facts["transaction_date"].hour

    return {
        "local_time": facts["transaction_date"].strftime("%H:%M"),
        "hour_band": hour_band(hour),
        "pix_in_band": history["same_hour_band_count"],
        "trusted_pix": history["trusted_count"],
        "history_days": HISTORY_DAYS,
    }


def _amount_ratio_between(facts: dict, low: float, high: Optional[float]) -> Optional[dict]:
    history = facts["history"]

    if history["trusted_count"] < MIN_TICKET_HISTORY or not history["average_ticket"]:
        return None

    ratio = facts["amount"] / history["average_ticket"]

    if ratio < low:
        return None

    if high is not None and ratio >= high:
        return None

    return {"amount": facts["amount"], "average_ticket": history["average_ticket"], "ratio": round(ratio, 1)}


def _burst_count(facts: dict) -> int:
    return facts["history"]["burst_count"] + 1


def _metrics_summary(metric_scores: List[dict]) -> str:
    weighed = [f"{metric['label']} {metric['points']}" for metric in metric_scores if metric["points"] > 0]

    if not weighed:
        return ""

    return ". Pesaram: " + ", ".join(weighed)
