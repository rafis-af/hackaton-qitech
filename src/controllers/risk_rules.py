"""O motor de decisão: o risco de cada Pix, de 0 a 100, e o que ele decide.

────────────────────────────────────────────────────────────────────
A TESE
────────────────────────────────────────────────────────────────────
Cada Pix é comparado com o histórico do PRÓPRIO cliente. A pergunta não
é "isso é estranho?", é "isso é estranho para esta pessoa?". Um Pix às
3h da manhã é normal para quem trabalha de madrugada e estranho para
quem sempre pagou de dia.

O risco de um Pix é a soma de seis métricas, e NENHUMA DECIDE SOZINHA:

    horário     até 35   o cliente costuma pagar nesta hora?
    valor       até 30   o valor cabe no ticket médio dele?
    local       até 25   saiu de perto dos lugares de costume? (Modo Rua)
    velocidade  até 20   quantos Pix ele fez nos últimos minutos?
    destino     até 15   ele já pagou esta conta? ela parece laranja?
    origem      até 10   o aparelho e o IP batem com a conta?

        0 a 39    approved
       40 a 69    two_factor_required
       70 a 100   rejected

O maior teto é 35, abaixo do 40 do 2FA: nenhuma métrica sozinha pede
nem a selfie. Duas fortes pedem 2FA; reprovar exige três. O banco cobra
isso num CHECK da tabela risk_metric, então não é só uma intenção.

O horário pesa mais que o local de propósito: o celular roubado é usado
quando o dono não usaria, e isso pesa mais do que de onde ele é usado.

────────────────────────────────────────────────────────────────────
O RISCO TEM PRAZO DE VALIDADE
────────────────────────────────────────────────────────────────────
Um Pix arriscado deixa memória por 24 horas: o Pix seguinte do mesmo
cliente herda parte do risco dele. É o que pega quem insiste.

    2FA confirmado           herda 0    a dúvida foi tirada pelo cliente
    2FA que falhou           herda tudo a dúvida virou suspeita
    o resto (aprovado,       herda metade
    pendente, reprovado...)

A herança vem do MAIOR risco ainda válido, e sempre do risco do próprio
Pix anterior (nunca do que ele mesmo herdou), para não crescer sozinha.
Passadas 24 horas, o risco vence e não pesa mais em nada.

────────────────────────────────────────────────────────────────────
FATOS NÃO SÃO MÉTRICAS
────────────────────────────────────────────────────────────────────
Documento que não existe, lista restritiva, CPF de quem recebe cancelado
ou de titular falecido na Receita: isso não é comportamento, é fato, e
reprova direto. Nenhuma métrica de comportamento decide sozinha; fatos
objetivos, sim.

────────────────────────────────────────────────────────────────────
POR QUE FUNÇÕES PURAS
────────────────────────────────────────────────────────────────────
Nada aqui fala com o banco. O controller busca os números (os "fatos")
e entrega prontos; cada sinal só compara. Assim cada um é testado sem
banco (tests/unit/test_risk_rules.py), e a decisão é determinística.

Os PONTOS de cada sinal e o TETO de cada métrica moram no banco (tabelas
rule e risk_metric). Os LIMIARES moram aqui, ao lado da conta que os usa,
cada um com o seu porquê.
"""

import math
from datetime import timedelta
from typing import Callable, Dict, List, Optional

from models import Analysis, AnalysisStatus, DocumentSituation, Rule, TwoFactorResult

# ────────────────────────────────────────────────────────────────────
# A escala e as faixas
# ────────────────────────────────────────────────────────────────────

MAX_RISK = 100
TWO_FACTOR_FROM = 40
REJECT_FROM = 70

# O "normal" do cliente: três meses de histórico. Só Pix CONFIÁVEIS
# entram nessa conta (aprovados direto, ou com 2FA confirmado): o que o
# ladrão fez não pode virar o normal de ninguém.
HISTORY_DAYS = 90

# ────────────────────────────────────────────────────────────────────
# Horário (até 35)
# ────────────────────────────────────────────────────────────────────
# A faixa de um Pix é a hora dele, uma antes e uma depois: quem sempre
# paga às 14h não fica "fora do padrão" por pagar às 15h.
HOUR_BAND_WIDTH = 1
# Abaixo de 5 Pix, não dá para dizer qual é o horário de costume.
MIN_HOUR_HISTORY = 5
# Menos de 10% dos Pix naquela faixa: horário raro para o cliente.
RARE_HOUR_SHARE = 0.10
# Sem histórico, vale a referência de todo mundo: o período noturno do
# Banco Central (Resolução BCB nº 142/2021), das 20h às 6h.
NIGHT_START_HOUR = 20
NIGHT_END_HOUR = 6

# ────────────────────────────────────────────────────────────────────
# Valor (até 30)
# ────────────────────────────────────────────────────────────────────
# Com menos de 3 Pix, a média não diz nada.
MIN_TICKET_HISTORY = 3
AMOUNT_RATIO_HIGH = 5
AMOUNT_RATIO_MEDIUM = 3
AMOUNT_RATIO_LOW = 2
# Sem ticket médio, vale o limite noturno do Banco Central como
# referência de "valor alto": R$ 1.000.
HIGH_AMOUNT_WITHOUT_HISTORY = 100000

# ────────────────────────────────────────────────────────────────────
# Local (até 25) — o Modo Rua
# ────────────────────────────────────────────────────────────────────
# Os lugares de costume são de onde saíram os Pix confiáveis do cliente
# nos últimos 90 dias. 30 km cobre uma região metropolitana (casa,
# trabalho, faculdade) e já separa uma cidade da outra.
#
# A distância é até o lugar MAIS PERTO, e não até a média: quem mora em
# São Paulo e trabalha em Campinas (85 km) teria a média no meio da
# estrada, longe dos dois.
STREET_MODE_RADIUS_KM = 30
EARTH_RADIUS_KM = 6371

# ────────────────────────────────────────────────────────────────────
# Velocidade (até 20)
# ────────────────────────────────────────────────────────────────────
# O ladrão tem pressa: ele sabe que a vítima vai bloquear o celular.
# Três amigos devolvendo a conta do bar já dão três Pix, então três é
# só um sinal; quatro já é rajada.
BURST_WINDOW_MINUTES = 10
BURST_MEDIUM = 3
BURST_HIGH = 4

# ────────────────────────────────────────────────────────────────────
# Destino (até 15)
# ────────────────────────────────────────────────────────────────────
NEW_ACCOUNT_DAYS = 30                  # conta laranja costuma ser aberta para o golpe
DESTINATION_PAYERS_THRESHOLD = 5       # muitas vítimas pagando a mesma conta
DESTINATION_PAYERS_WINDOW_HOURS = 24

# A Receita, sobre quem recebe. Suspenso ou pendente tem caso legítimo
# (quem não declarou o imposto), então é só um sinal de destino. Os casos
# sem saída legítima (não existe, cancelado, nulo, falecido) são fato, e
# reprovam direto.
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

# ────────────────────────────────────────────────────────────────────
# Origem (até 10)
# ────────────────────────────────────────────────────────────────────
# Premissa do projeto: um aparelho por conta e uma conta por aparelho.
# Troca de aparelho é legítima (celular novo), mas é também o momento do
# golpe da troca de aparelho, então pesa nas 24h seguintes.
DEVICE_CHANGE_WINDOW_HOURS = 24
DEVICE_SHARED_WINDOW_DAYS = 30
# IP é um sinal fraco: operadoras de celular põem milhares de clientes
# atrás do mesmo IP. Por isso a janela é curta e o teto da métrica é 10.
IP_SHARED_WINDOW_HOURS = 24

# ────────────────────────────────────────────────────────────────────
# A memória do risco
# ────────────────────────────────────────────────────────────────────
MEMORY_HOURS = 24
CARRY_WHEN_CONFIRMED = 0.0
CARRY_WHEN_FAILED = 1.0
CARRY_OTHERWISE = 0.5


# ────────────────────────────────────────────────────────────────────
# Os sinais. Cada um devolve o que viu (vira `observed`) ou None.
# ────────────────────────────────────────────────────────────────────

# Fatos

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
    """Se a Receita não respondeu, a situação é None e nada dispara: falta de dado externo nunca reprova."""
    situation = facts["destination"]["receita_situation"]

    if situation not in RECEITA_BLOCKED_SITUATIONS:
        return None

    return {"situation": situation}


# Horário

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


# Valor

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


# Local

def check_far_from_usual_places(facts: dict) -> Optional[dict]:
    """O Modo Rua. Não roda quando não há como comparar.

    Pix sem geolocalização (ela é opcional no contrato) ou cliente sem
    nenhum lugar de costume ainda: nada a dizer sobre o local.
    """
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


# Velocidade

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


# Destino

def check_new_destination(facts: dict) -> Optional[dict]:
    """Conta que o cliente nunca pagou de verdade (aprovado ou 2FA confirmado)."""
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


# Origem

def check_device_changed(facts: dict) -> Optional[dict]:
    """O aparelho é outro que o do último Pix, ou houve outro aparelho nas últimas 24h.

    Primeiro Pix da vida não conta: não há aparelho antigo para comparar.
    """
    last_device_id = facts["last_device_id"]

    changed_now = last_device_id is not None and last_device_id != facts["device_id"]
    changed_recently = facts["history"]["other_device_in_short_window"]

    if not changed_now and not changed_recently:
        return None

    # Só o FATO da troca, e não o identificador do aparelho anterior: a
    # análise é consultável pelo GET, e o id de outro aparelho do cliente
    # não ajuda ninguém a entender a decisão (minimização, LGPD). O mesmo
    # vale para device_shared e ip_shared, que mostram só a contagem.
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


# Liga o nome do sinal no banco à função que o avalia. Sinal que existe
# no banco e não aqui (ou o contrário) é avisado no log pelo controller;
# sinal desligado no banco (rule.active = false) não roda.
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


# ────────────────────────────────────────────────────────────────────
# Dos sinais ao risco, e do risco à decisão
# ────────────────────────────────────────────────────────────────────

def evaluate(rules: list, facts: dict) -> List[dict]:
    """Roda cada sinal ligado e devolve os que dispararam, na ordem do catálogo."""
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
    """Quanto cada métrica pesou neste Pix: a soma dos sinais dela, com o teto da métrica.

    Volta TODAS as métricas, inclusive as que ficaram em zero: "o horário
    não pesou nada" também é explicação.
    """
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
    """O risco que os Pix das últimas 24 horas deixam para este.

    Cada anterior deixa uma parte do PRÓPRIO risco, conforme o que
    aconteceu com o 2FA dele; vale a maior. `previous_analyses` já vem
    filtrado pela janela de validade.
    """
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
    """Transforma o risco na decisão. A ordem é a regra:

      1. algum fato reprova direto                  → rejected
      2. risco = risco deste Pix + herdado, teto 100
           70 a 100  → rejected
           40 a 69   → two_factor_required
            0 a 39   → approved

    A decisão sai com o MOTIVO: o que decidiu, quais sinais causaram e uma
    frase que qualquer pessoa lê. É o que fica gravado em todo Pix,
    inclusive no reprovado.
    """
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


# ────────────────────────────────────────────────────────────────────
# Ferramentas
# ────────────────────────────────────────────────────────────────────

def is_night(hour: int) -> bool:
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def hour_band(hour: int) -> List[int]:
    """As horas que contam como "a mesma faixa": a hora do Pix, uma antes e uma depois.

    Dá a volta no relógio: a faixa da meia-noite é 23h, 0h e 1h.
    """
    return sorted({(hour + offset) % 24 for offset in range(-HOUR_BAND_WIDTH, HOUR_BAND_WIDTH + 1)})


def distance_km(origin: tuple, destination: tuple) -> float:
    """A distância entre dois pontos (latitude, longitude) sobre a Terra, em km.

    É a fórmula de Haversine: trata a Terra como uma esfera. O erro em
    relação ao formato real fica abaixo de 0,5% — para decidir se o Pix
    saiu a mais ou a menos de 30 km de casa, sobra precisão.
    """
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
    # O histórico conta os ANTERIORES; este Pix é mais um.
    return facts["history"]["burst_count"] + 1


def _metrics_summary(metric_scores: List[dict]) -> str:
    weighed = [f"{metric['label']} {metric['points']}" for metric in metric_scores if metric["points"] > 0]

    if not weighed:
        return ""

    return ". Pesaram: " + ", ".join(weighed)
