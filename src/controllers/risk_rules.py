"""O motor de decisão: cada regra é uma função pura, e a decisão sai da soma.

────────────────────────────────────────────────────────────────────
A TESE
────────────────────────────────────────────────────────────────────
Valor alto isolado não é fraude; o padrão repetido é. Quem paga o
aluguel de R$ 5.000 para o mesmo destino todo mês não é suspeito. Quem
faz dez Pix de R$ 900 à noite para dez contas novas é.

Por isso o motor não olha só o Pix que chegou: ele soma os pontos que o
pagador acumulou nos últimos 7 dias. Os pontos expiram sozinhos, porque
o score é CALCULADO a cada Pix (a soma da janela) e não guardado.

────────────────────────────────────────────────────────────────────
OS TRÊS TIPOS DE REGRA (a coluna `rule.action`)
────────────────────────────────────────────────────────────────────
• reject      — o risco é claro sem precisar de histórico: documento que
                não existe, destino na lista restritiva, cliente Prata
                passando do limite. Reprova direto.
• two_factor  — FALTA DE DADO (pagador sem histórico, nível não
                informado) ou uma condição que o PRÓPRIO CLIENTE ligou
                (o Modo Rua). Nada disso é comportamento suspeito, então
                não soma ponto e nunca reprova: só pede confirmação.
• score       — COMPORTAMENTO. Soma pontos, e os pontos ficam 7 dias.

A separação entre as duas últimas é o que impede o motor de punir quem
só é novo, ou quem só está na rua. Fricção não deixa marca; comportamento
suspeito vira ponto.

────────────────────────────────────────────────────────────────────
POR QUE FUNÇÕES PURAS
────────────────────────────────────────────────────────────────────
Nada aqui fala com o banco. O controller busca os números (os "fatos")
e entrega prontos; cada regra só compara. Assim cada uma é testada sem
banco, com um dicionário na mão (tests/unit/test_risk_rules.py), e a
decisão é determinística: o mesmo fato dá sempre a mesma resposta.

Os PONTOS de cada regra e se ela está ligada moram na tabela `rule`. Os
LIMIARES moram aqui, ao lado da conta que os usa, e cada um tem o seu
porquê escrito.
"""

from datetime import timedelta
from typing import Callable, Dict, List, Optional

from models import Analysis, AnalysisStatus, Rule

# ────────────────────────────────────────────────────────────────────
# Os parâmetros, cada um com o seu porquê
# ────────────────────────────────────────────────────────────────────

# Janelas de tempo
SCORE_WINDOW_DAYS = 7          # quanto tempo um ponto pesa: uma semana de comportamento
HISTORY_DAYS = 90              # o "perfil" do pagador: três meses bastam para um ticket médio
SHORT_WINDOW_HOURS = 24        # rajada: o golpe acontece em horas, não em semanas

# Escala do score
MAX_SCORE = 5
TWO_FACTOR_FROM_SCORE = 2      # 0 e 1 passam: um sinal fraco sozinho não trava ninguém
REJECT_AT_SCORE = 5

# Limite noturno do Banco Central (Resolução BCB nº 142/2021): R$ 1.000
# entre 20h e 6h para pessoa física. Vale só para CPF.
NIGHT_START_HOUR = 20
NIGHT_END_HOUR = 6
NIGHT_LIMIT = 100000

# Limite diário do Prata na API Banco (RFC da fase 1).
SILVER_DAILY_LIMIT = 100000

# Até este valor, pagador sem histórico passa sem 2FA: é um gasto do
# dia a dia (mercado, uma conta pequena). Valor inicial, a calibrar.
NEW_PAYER_FREE_AMOUNT = 20000

# Fracionamento: Pix logo abaixo do limite noturno, repetidos. Quem
# divide R$ 3.000 em quatro de R$ 900 está fugindo do teto.
SPLIT_MIN_AMOUNT = 80000
SPLIT_MAX_AMOUNT = 100000
SPLIT_COUNT_THRESHOLD = 3

# Muitos destinos novos em pouco tempo: conta tomada esvaziando o saldo,
# ou conta laranja repassando. Cinco em 24h é raro para quem só divide a
# conta do bar.
NEW_DESTINATIONS_THRESHOLD = 5

# Perfil: só faz sentido comparar com o padrão de quem TEM padrão.
PROFILE_MIN_HISTORY = 3
PROFILE_TICKET_MULTIPLIER = 3
DAILY_SUM_MULTIPLIER = 5
DAILY_SUM_MIN_AMOUNT = 100000  # abaixo de R$ 1.000 no dia, 5x o ticket ainda é troco

# Contraparte
NEW_ACCOUNT_DAYS = 30                  # conta laranja costuma ser aberta para o golpe
DESTINATION_PAYERS_THRESHOLD = 5       # muitas vítimas pagando a mesma conta

# Aparelho. Premissa do projeto: o banco libera UM aparelho por conta e
# UMA conta por aparelho. Para o antifraude, isso dá dois sinais:
#  • aparelho trocado — a conta passou para outro celular. É legítimo
#    (celular novo, quebrado), mas é também o golpe da troca de aparelho;
#    por isso pesa nas 24 horas seguintes à troca.
#  • aparelho compartilhado — o mesmo celular aparecendo em outra conta
#    quebra a premissa: app adulterado ou identificador clonado.
DEVICE_CHANGE_WINDOW_HOURS = SHORT_WINDOW_HOURS
DEVICE_SHARED_WINDOW_DAYS = 30


# ────────────────────────────────────────────────────────────────────
# As regras. Cada uma devolve o que viu (vira `observed`) ou None.
# ────────────────────────────────────────────────────────────────────

def check_invalid_document_number(facts: dict) -> Optional[dict]:
    invalid_fields = []

    if not facts["payer"]["document_is_valid"]:
        invalid_fields.append("source_account.owner.document_number")

    if not facts["destination"]["document_is_valid"]:
        invalid_fields.append("destination_account.owner.document_number")

    if not invalid_fields:
        return None

    return {"invalid_fields": invalid_fields}


def check_restricted_destination(facts: dict) -> Optional[dict]:
    reason = facts["destination"]["restricted_reason"]

    if reason is None:
        return None

    return {"reason": reason}


def check_silver_daily_limit_exceeded(facts: dict) -> Optional[dict]:
    if facts["payer"]["level"] != "silver":
        return None

    return _daily_limit_observed(facts)


def check_unknown_level_daily_limit(facts: dict) -> Optional[dict]:
    if facts["payer"]["level"] is not None:
        return None

    observed = _daily_limit_observed(facts)

    if observed is not None:
        observed["assumed_level"] = "silver"

    return observed


def check_payer_without_history(facts: dict) -> Optional[dict]:
    if facts["history"]["pix_count"] > 0:
        return None

    if facts["amount"] <= NEW_PAYER_FREE_AMOUNT:
        return None

    return {"amount": facts["amount"], "threshold": NEW_PAYER_FREE_AMOUNT, "history_days": HISTORY_DAYS}


def check_street_mode(facts: dict) -> Optional[dict]:
    """O Modo Rua: o cliente saiu das redes Wi-Fi que ele cadastrou no app.

    Quem cadastra as redes e percebe que o celular saiu delas é o app; o
    antifraude recebe só o resultado. Com o modo ativo, o Pix pede 2FA:
    todos, se o cliente não escolheu limite, ou só os acima do limite.
    Celular roubado na rua passa a exigir a selfie do dono.
    """
    street_mode = facts["street_mode"]

    if street_mode is None or not street_mode["active"]:
        return None

    limit = street_mode["limit"]

    if limit is not None and facts["amount"] <= limit:
        return None

    return {"amount": facts["amount"], "limit": limit}


def check_night_limit(facts: dict) -> Optional[dict]:
    if not facts["payer"]["is_person"]:
        return None

    if not is_night(facts["transaction_date"].hour):
        return None

    if facts["amount"] <= NIGHT_LIMIT:
        return None

    return {
        "amount": facts["amount"],
        "threshold": NIGHT_LIMIT,
        "local_time": facts["transaction_date"].strftime("%H:%M"),
    }


def check_many_new_destinations(facts: dict) -> Optional[dict]:
    new_destinations = facts["new_destinations"]

    if new_destinations < NEW_DESTINATIONS_THRESHOLD:
        return None

    return {"value": new_destinations, "threshold": NEW_DESTINATIONS_THRESHOLD, "window_hours": SHORT_WINDOW_HOURS}


def check_split_below_night_limit(facts: dict) -> Optional[dict]:
    if not is_night(facts["transaction_date"].hour):
        return None

    if not SPLIT_MIN_AMOUNT <= facts["amount"] <= SPLIT_MAX_AMOUNT:
        return None

    # O histórico conta os ANTERIORES; este Pix é mais um.
    split_count = facts["history"]["split_count"] + 1

    if split_count < SPLIT_COUNT_THRESHOLD:
        return None

    return {
        "value": split_count,
        "threshold": SPLIT_COUNT_THRESHOLD,
        "amount_range": [SPLIT_MIN_AMOUNT, SPLIT_MAX_AMOUNT],
        "window_hours": SHORT_WINDOW_HOURS,
    }


def check_amount_above_profile(facts: dict) -> Optional[dict]:
    """Valor alto para destino NOVO. Para destino conhecido, valor alto é só o aluguel."""
    history = facts["history"]

    if history["accepted_count"] < PROFILE_MIN_HISTORY or history["known_destination"]:
        return None

    limit = history["average_ticket"] * PROFILE_TICKET_MULTIPLIER

    if facts["amount"] <= limit:
        return None

    return {"amount": facts["amount"], "average_ticket": history["average_ticket"], "multiplier": PROFILE_TICKET_MULTIPLIER}


def check_daily_sum_above_profile(facts: dict) -> Optional[dict]:
    history = facts["history"]

    if history["accepted_count"] < PROFILE_MIN_HISTORY:
        return None

    total = history["sum_short_window"] + facts["amount"]
    limit = max(history["average_ticket"] * DAILY_SUM_MULTIPLIER, DAILY_SUM_MIN_AMOUNT)

    if total <= limit:
        return None

    return {
        "sum": total,
        "average_ticket": history["average_ticket"],
        "multiplier": DAILY_SUM_MULTIPLIER,
        "window_hours": SHORT_WINDOW_HOURS,
    }


def check_device_changed(facts: dict) -> Optional[dict]:
    """A conta passou para outro aparelho: agora, ou nas últimas 24 horas.

    Com um aparelho por conta, aparelho diferente do último quer dizer
    que a conta foi ativada num celular novo. O sinal continua nas 24
    horas seguintes, e não só no primeiro Pix depois da troca: o golpe
    da troca de aparelho esvazia a conta em sequência.

    Pagador sem nenhum Pix anterior não dispara: não há aparelho antigo
    para comparar, e essa falta de dado é do payer_without_history.
    """
    last_device_id = facts["last_device_id"]

    changed_now = last_device_id is not None and last_device_id != facts["device_id"]
    changed_recently = facts["history"]["other_device_in_short_window"]

    if not changed_now and not changed_recently:
        return None

    return {"previous_device_id": last_device_id, "window_hours": DEVICE_CHANGE_WINDOW_HOURS}


def check_device_shared(facts: dict) -> Optional[dict]:
    other_payers = facts["other_payers_on_device"]

    if other_payers == 0:
        return None

    return {"other_payers": other_payers, "window_days": DEVICE_SHARED_WINDOW_DAYS}


def check_new_destination_account(facts: dict) -> Optional[dict]:
    account_age = facts["transaction_date"] - facts["destination"]["opening_date"]

    if account_age >= timedelta(days=NEW_ACCOUNT_DAYS):
        return None

    return {"account_age_days": account_age.days, "threshold_days": NEW_ACCOUNT_DAYS}


def check_destination_many_payers(facts: dict) -> Optional[dict]:
    payers = facts["destination_payers"]

    if payers < DESTINATION_PAYERS_THRESHOLD:
        return None

    return {"value": payers, "threshold": DESTINATION_PAYERS_THRESHOLD, "window_hours": SHORT_WINDOW_HOURS}


def check_two_factor_failed_recently(facts: dict) -> Optional[dict]:
    failures = facts["history"]["failed_two_factor_count"]

    if failures == 0:
        return None

    return {"failures": failures, "window_hours": SHORT_WINDOW_HOURS}


# Liga o nome da regra no banco à função que a avalia. Regra que existe
# no banco e não aqui é ignorada; regra que existe aqui e está desligada
# no banco (rule.active = false) não roda.
RULE_CHECKS: Dict[str, Callable[[dict], Optional[dict]]] = {
    "invalid_document_number": check_invalid_document_number,
    "restricted_destination": check_restricted_destination,
    "silver_daily_limit_exceeded": check_silver_daily_limit_exceeded,
    "unknown_level_daily_limit": check_unknown_level_daily_limit,
    "payer_without_history": check_payer_without_history,
    "street_mode": check_street_mode,
    "night_limit": check_night_limit,
    "many_new_destinations": check_many_new_destinations,
    "split_below_night_limit": check_split_below_night_limit,
    "amount_above_profile": check_amount_above_profile,
    "device_changed": check_device_changed,
    "device_shared": check_device_shared,
    "daily_sum_above_profile": check_daily_sum_above_profile,
    "new_destination_account": check_new_destination_account,
    "destination_many_payers": check_destination_many_payers,
    "two_factor_failed_recently": check_two_factor_failed_recently,
}


# ────────────────────────────────────────────────────────────────────
# Da lista de regras à decisão
# ────────────────────────────────────────────────────────────────────

def evaluate(rules: list, facts: dict) -> List[dict]:
    """Roda cada regra ligada e devolve as que dispararam, na ordem do catálogo."""
    signals = []

    for rule in rules:
        check = RULE_CHECKS.get(rule.enumerator)

        if check is None:
            continue

        observed = check(facts)

        if observed is None:
            continue

        signals.append(
            {
                "rule": rule.enumerator,
                "family": rule.family,
                "action": rule.action,
                "points": rule.points,
                "description": rule.description,
                "observed": observed,
            }
        )

    return signals


def decide(signals: List[dict], previous_points: int) -> dict:
    """Transforma os sinais na decisão. A ordem é a regra:

      1. alguma regra de bloqueio disparou      → rejected
      2. score = pontos da janela + deste Pix, com teto de 5
           5        → rejected
           2 a 4    → two_factor_required
           0 ou 1   → approved
      3. aprovado, mas uma regra de 2FA disparou → two_factor_required

    O passo 3 só SOBE a decisão, nunca desce: falta de dado e Modo Rua
    transformam um approved em 2FA, mas nunca livram ninguém de um
    rejected.

    A decisão sai com o MOTIVO: qual dos três passos decidiu, quais
    regras causaram a decisão e uma frase que qualquer pessoa lê. É o
    que fica gravado em todo Pix, inclusive no reprovado.
    """
    points_from_this_pix = 0
    reject_signals = []
    two_factor_signals = []
    score_signals = []

    for signal in signals:
        if signal["action"] == Rule.SCORE:
            points_from_this_pix = points_from_this_pix + signal["points"]
            score_signals.append(signal)
        elif signal["action"] == Rule.REJECT:
            reject_signals.append(signal)
        elif signal["action"] == Rule.TWO_FACTOR:
            two_factor_signals.append(signal)

    risk_score = min(MAX_SCORE, previous_points + points_from_this_pix)
    score_summary = (
        f"score de risco {risk_score} de {MAX_SCORE}: {previous_points} ponto(s) dos últimos "
        f"{SCORE_WINDOW_DAYS} dias + {points_from_this_pix} deste Pix"
    )

    if reject_signals:
        status = AnalysisStatus.REJECTED
        reason = Analysis.BLOCKING_RULE
        decisive_signals = reject_signals
        description = "Reprovado por regra de bloqueio: " + _join_descriptions(reject_signals)
    elif risk_score >= REJECT_AT_SCORE:
        status = AnalysisStatus.REJECTED
        reason = Analysis.RISK_SCORE
        decisive_signals = score_signals
        description = f"Reprovado: {score_summary}"
    elif risk_score >= TWO_FACTOR_FROM_SCORE:
        status = AnalysisStatus.TWO_FACTOR_REQUIRED
        reason = Analysis.RISK_SCORE
        decisive_signals = score_signals
        description = f"2FA pedido: {score_summary}"
    elif two_factor_signals:
        status = AnalysisStatus.TWO_FACTOR_REQUIRED
        reason = Analysis.TWO_FACTOR_RULE
        decisive_signals = two_factor_signals
        description = "2FA pedido: " + _join_descriptions(two_factor_signals)
    else:
        status = AnalysisStatus.APPROVED
        reason = Analysis.RISK_SCORE
        decisive_signals = []
        description = f"Aprovado: {score_summary}"

    return {
        "status": status,
        "reason": reason,
        "decisive_rules": [signal["rule"] for signal in decisive_signals],
        "description": description,
        "risk_score": risk_score,
        "previous_points": previous_points,
        "points_from_this_pix": points_from_this_pix,
    }


def _join_descriptions(signals: List[dict]) -> str:
    return "; ".join(signal["description"] for signal in signals)


def is_night(hour: int) -> bool:
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def _daily_limit_observed(facts: dict) -> Optional[dict]:
    total = facts["history"]["sum_today"] + facts["amount"]

    if total <= SILVER_DAILY_LIMIT:
        return None

    return {"sum_today": total, "threshold": SILVER_DAILY_LIMIT}
