#!/usr/bin/env python3
import html
import json
import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from datetime import datetime, timezone
from pathlib import Path


STATUS_PATH = Path("data/status.json")
STATE_PATH = Path("data/email_notification_state.json")
MIN_NOTIFY_LEVEL = 3
CONTEXT_CHANGE_COOLDOWN_HOURS = 3

LEVEL_LABELS = {
    1: "Vigilância",
    2: "Observação",
    3: "Atenção",
    4: "Alerta",
    5: "Alerta Máximo",
}

BASE_ACTIONS = {
    3: [
        "Colocar Infraestrutura e Operações em pré-alerta.",
        "Verificar água, energia, acessos e recursos de continuidade.",
    ],
    4: [
        "Formalizar quadro de situação.",
        "Verificar gatilhos dos planos institucionais.",
        "Avaliar critérios institucionais para acionamento do Comitê de Crise.",
    ],
    5: [
        "Direção deve avaliar imediatamente os impactos reais.",
        "Executar os planos aplicáveis quando seus gatilhos internos forem atingidos.",
        "Avaliar e, quando houver critério institucional, acionar o Comitê de Crise.",
    ],
}

CONTEXT_ACTIONS = {
    "rain": [
        "Tratar água, energia e acessos de forma conjunta.",
        "Verificar geradores, QTA, nobreaks, diesel e possível falha da rede elétrica externa.",
        "Acompanhar alagamentos, enchentes, quedas de árvores e bloqueios viários.",
    ],
    "wind": [
        "Verificar continuidade elétrica e possíveis interferências na rede externa.",
        "Acompanhar quedas de árvores e condições dos acessos.",
    ],
    "geo": [
        "Acompanhar encostas e risco de deslizamentos.",
        "Verificar acessos alternativos e impactos sobre infraestrutura.",
    ],
    "road": [
        "Acompanhar condições da BR-040/495 e demais acessos relevantes.",
        "Antecipar impactos para equipes, pacientes, fornecedores e suprimentos.",
    ],
}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clean_text(value, max_len=2000):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_len] if text else "—"


def numeric(value):
    try:
        return float(value)
    except Exception:
        return None


def derive_context(data):
    sources = data.get("sources") or {}
    geo = sources.get("cemaden_geological") or {}
    hydro = sources.get("cemaden_hydrological") or {}
    inmet = sources.get("inmet_alerts") or {}
    defesa = sources.get("defesa_civil") or {}
    roads = data.get("roads") or {}
    overall = data.get("overall") or {}
    pv = data.get("pluviometers") or {}
    forecast = data.get("forecast") or {}

    parts = [
        overall.get("reason"),
        geo.get("risk"), geo.get("message"),
        hydro.get("risk"), hydro.get("message"),
        inmet.get("risk"), inmet.get("title"),
        defesa.get("stage"), defesa.get("basis"), defesa.get("message"),
        (defesa.get("operational_signal") or {}).get("label"),
        roads.get("alert_state"), roads.get("message"),
    ]
    parts.extend(overall.get("supplemental_signals") or [])
    for day in (forecast.get("days") or [])[:2]:
        parts.append((day or {}).get("summary"))
    text = " ".join(str(x) for x in parts if x).lower()

    h1 = numeric((pv.get("highest_1h") or {}).get("value"))
    h24 = numeric((pv.get("highest_24h") or {}).get("value"))

    rain = (
        (hydro.get("level") or 0) > 1
        or (h1 is not None and h1 >= 20)
        or (h24 is not None and h24 >= 50)
        or any(t in text for t in ("chuva", "pancada", "tempestade", "precipita", "alag", "enchent", "inunda", "hidrol"))
    )
    wind = any(t in text for t in ("vento forte", "rajada", "vendaval", "queda de árvore", "queda de arvore"))
    geological = (geo.get("level") or 0) > 1 or any(t in text for t in ("desliz", "movimento de massa", "geológ", "geolog"))
    road = any(t in text for t in ("interdi", "bloque", "rodovia", "br-040", "br-495", "trânsito", "transito"))

    if rain:
        return "rain", "Chuva · Água + Energia + Acessos"
    if wind:
        return "wind", "Vento · Energia + Acessos"
    if geological:
        return "geo", "Geológico · Acessos + Infraestrutura"
    if road:
        return "road", "Acessos · Logística"
    return "general", "Monitoramento geral"


def actions_for(level, context_key):
    items = []
    items.extend(CONTEXT_ACTIONS.get(context_key, []))
    items.extend(BASE_ACTIONS.get(level, []))
    unique = []
    for item in items:
        if item not in unique:
            unique.append(item)
    if not unique:
        unique = ["Manter monitoramento e seguir as ações prioritárias exibidas no HCNSC Alerta."]
    return unique[:5]


def hours_since(value):
    if not value:
        return 9999
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600
    except Exception:
        return 9999


def determine_event(level, context_key, state, min_level, force_test=False):
    if force_test:
        return "test"

    last_level = state.get("last_notified_level")
    last_context = state.get("last_notified_context")

    if last_level is None:
        return "entry" if level >= min_level else None

    last_level = int(last_level)

    # If this group was previously notified and the HCNSC level leaves the
    # group's notification range, send one recovery message before clearing state.
    if level < min_level:
        return "recovery"

    if level > last_level:
        return "escalation"
    if level < last_level:
        return "deescalation"
    if context_key != last_context and hours_since(state.get("last_sent_at")) >= CONTEXT_CHANGE_COOLDOWN_HOURS:
        return "context_change"
    return None


def parse_recipients(raw):
    parts = re.split(r"[,;\n]+", raw or "")
    recipients = []
    for item in parts:
        email = item.strip()
        if email and email not in recipients:
            recipients.append(email)
    return recipients



def config():
    return {
        "groups": {
            "operational": {
                "label": "Grupo Operacional",
                "levels": [3, 4, 5],
                "min_level": 3,
                "recipients": parse_recipients(os.getenv("HCNSC_EMAIL_GRUPO_OPERACIONAL", "")),
            },
            "managers": {
                "label": "Grupo de Gerentes",
                "levels": [4, 5],
                "min_level": 4,
                "recipients": parse_recipients(os.getenv("HCNSC_EMAIL_GRUPO_GERENTES", "")),
            },
        },
        "smtp_user": os.getenv("HCNSC_SMTP_USER", "").strip(),
        "smtp_app_password": os.getenv("HCNSC_SMTP_APP_PASSWORD", "").strip(),
        "smtp_host": os.getenv("HCNSC_SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com",
        "smtp_port": int(os.getenv("HCNSC_SMTP_PORT", "465").strip() or "465"),
        "from_email": os.getenv("HCNSC_EMAIL_FROM", "").strip(),
        "reply_to": os.getenv("HCNSC_EMAIL_REPLY_TO", "").strip(),
        "public_url": os.getenv("HCNSC_ALERTA_PUBLIC_URL", "https://diogomantovani.github.io/hcnsc-alerta/").strip(),
    }



def configured(cfg, group):
    return bool(group.get("recipients") and provider_name(cfg))



def provider_name(cfg):
    if cfg.get("smtp_user") and cfg.get("smtp_app_password"):
        return "smtp"
    return None


def event_label(event):
    return {
        "test": "TESTE",
        "entry": "NOVO ALERTA",
        "escalation": "AGRAVAMENTO",
        "deescalation": "ATUALIZAÇÃO",
        "context_change": "MUDANÇA DE CONTEXTO",
        "recovery": "MELHORA DO CENÁRIO",
    }.get(event, "ATUALIZAÇÃO")


def build_message(data, level, context_key, context_label, event, cfg, previous_level=None):
    overall = data.get("overall") or {}
    reason = clean_text(overall.get("reason") or "Atualização do monitoramento HCNSC.", 1400)
    if event == "recovery":
        actions = [
            "Manter o monitoramento pelo HCNSC Alerta.",
            "O cenário saiu da faixa automática de aviso deste grupo.",
        ]
    else:
        actions = actions_for(level, context_key)
    generated_at = data.get("generated_at") or now_iso()
    label = LEVEL_LABELS.get(level, "Nível HCNSC")
    previous_label = LEVEL_LABELS.get(previous_level, "Nível HCNSC") if previous_level else None
    event_text = event_label(event)

    subject = f"HCNSC Alerta | {event_text} | Nível {level} · {label}"

    transition_html = ""
    transition_text = ""
    if previous_level is not None and previous_level != level:
        transition_html = (
            '<div style="margin-bottom:20px;padding:12px 14px;background:#eef6f6;border-radius:10px">'
            f'<strong>Nível anterior:</strong> {previous_level} · {html.escape(previous_label)}<br>'
            f'<strong>Nível atual:</strong> {level} · {html.escape(label)}'
            '</div>'
        )
        transition_text = (
            f"Nível anterior: {previous_level} · {previous_label}\n"
            f"Nível atual: {level} · {label}\n\n"
        )

    action_html = "".join(f"<li style=\"margin:0 0 8px\">{html.escape(a)}</li>" for a in actions)
    action_text = "\n".join(f"- {a}" for a in actions)

    body_html = f"""<!doctype html>
<html lang="pt-BR">
<body style="margin:0;background:#f4f7f7;font-family:Arial,Helvetica,sans-serif;color:#173336">
  <div style="max-width:680px;margin:24px auto;background:#ffffff;border:1px solid #dce7e7;border-radius:14px;overflow:hidden">
    <div style="padding:22px 26px;background:#0b6f70;color:#ffffff">
      <div style="font-size:12px;font-weight:700;letter-spacing:.08em">{html.escape(event_text)}</div>
      <div style="font-size:25px;font-weight:800;margin-top:6px">HCNSC ALERTA · NÍVEL {level} · {html.escape(label)}</div>
    </div>
    <div style="padding:26px">
      {transition_html}
      <div style="font-size:13px;color:#617779;margin-bottom:5px">CONTEXTO OPERACIONAL</div>
      <div style="font-size:19px;font-weight:700;margin-bottom:20px">{html.escape(context_label)}</div>
      <div style="font-size:13px;color:#617779;margin-bottom:5px">MOTIVO</div>
      <div style="line-height:1.55;margin-bottom:22px">{html.escape(reason)}</div>
      <div style="font-size:13px;color:#617779;margin-bottom:5px">AÇÕES PRIORITÁRIAS</div>
      <ul style="padding-left:20px;line-height:1.45;margin-top:8px">{action_html}</ul>
      <div style="margin-top:24px;padding:14px 16px;background:#f1f7f7;border-radius:10px">
        <strong>Atualização:</strong> {html.escape(clean_text(generated_at,120))}
      </div>
      <div style="margin-top:22px">
        <a href="{html.escape(cfg['public_url'], quote=True)}" style="display:inline-block;background:#0b6f70;color:#ffffff;text-decoration:none;font-weight:700;padding:11px 16px;border-radius:8px">Abrir HCNSC Alerta</a>
      </div>
      <div style="margin-top:24px;font-size:12px;line-height:1.5;color:#65787a">
        Mensagem automática de apoio à decisão. Os documentos institucionais vigentes e as fontes oficiais permanecem soberanos.
      </div>
    </div>
  </div>
</body>
</html>"""

    body_text = f"""HCNSC ALERTA — {event_text}
{transition_text}Nível: {level} · {label}
Contexto: {context_label}

Motivo:
{reason}

Ações prioritárias:
{action_text}

Atualização: {generated_at}
Painel: {cfg['public_url']}

Mensagem automática de apoio à decisão. Os documentos institucionais vigentes e as fontes oficiais permanecem soberanos.
"""
    return subject, body_html, body_text


def send_email(cfg, recipients, subject, body_html, body_text):
    if not recipients:
        return False, [], "Nenhum destinatário configurado."
    if provider_name(cfg) != "smtp":
        return False, [], "Gmail SMTP não configurado."

    sent_ids = []
    failures = []

    for recipient in recipients:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.get("from_email") or formataddr(("HCNSC Alerta", cfg["smtp_user"]))
        msg["To"] = recipient
        if cfg.get("reply_to"):
            msg["Reply-To"] = cfg["reply_to"]
        msg.set_content(body_text)
        msg.add_alternative(body_html, subtype="html")
        try:
            with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as server:
                server.login(cfg["smtp_user"], cfg["smtp_app_password"])
                server.send_message(msg)
            sent_ids.append(None)
        except Exception as exc:
            failures.append(exc.__class__.__name__)

    if failures:
        return False, sent_ids, clean_text(
            f"{len(failures)} destinatário(s) com falha SMTP: " + ", ".join(failures),
            500,
        )
    return True, sent_ids, None


def update_public_status(data, state, cfg, enabled):
    notifications = data.setdefault("notifications", {})
    group_states = state.setdefault("groups", {})

    mapping = {
        "operational": "group_operational_email",
        "managers": "group_managers_email",
    }

    for key, public_key in mapping.items():
        group = cfg["groups"][key]
        group_state = group_states.setdefault(key, {})
        item = notifications.setdefault(public_key, {})
        item.update({
            "channel": "email",
            "label": group["label"],
            "levels": group["levels"],
            "recipient_configured": bool(group["recipients"]),
            "recipient_count": len(group["recipients"]),
            "provider_configured": bool(provider_name(cfg)),
            "provider": provider_name(cfg),
            "automatic_sending_enabled": bool(enabled),
            "status": (
                "active" if configured(cfg, group) and enabled
                else "ready_disabled" if configured(cfg, group)
                else "awaiting_recipient" if provider_name(cfg)
                else "awaiting_provider"
            ),
            "last_attempt_at": group_state.get("last_attempt_at"),
            "last_sent_at": group_state.get("last_sent_at"),
            "last_event": group_state.get("last_event"),
            "last_result": group_state.get("last_result"),
            "last_notified_level": group_state.get("last_notified_level"),
        })

    notifications.pop("group_operational_test", None)


def default_group_state():
    return {
        "last_notified_level": None,
        "last_notified_context": None,
        "last_sent_at": None,
        "last_attempt_at": None,
        "last_event": None,
        "last_result": None,
        "last_error": None,
        "last_message_id": None,
    }


def normalize_state(raw):
    raw = raw or {}
    if isinstance(raw.get("groups"), dict):
        state = raw
        state["schema_version"] = 2
        state.setdefault("groups", {})
        state["groups"].setdefault("operational", default_group_state())
        state["groups"].setdefault("managers", default_group_state())
        return state

    operational = default_group_state()
    for key in operational:
        if key in raw:
            operational[key] = raw.get(key)

    return {
        "schema_version": 2,
        "last_seen_level": raw.get("last_seen_level"),
        "last_seen_context": raw.get("last_seen_context"),
        "groups": {
            "operational": operational,
            "managers": default_group_state(),
        },
        "updated_at": raw.get("updated_at"),
    }



def main():
    data = load_json(STATUS_PATH, {})
    state = normalize_state(load_json(STATE_PATH, {}))

    cfg = config()
    enabled = env_bool("HCNSC_EMAIL_ENABLED", False)
    force_test = env_bool("HCNSC_EMAIL_FORCE_TEST", False)

    try:
        level = max(1, min(5, int((data.get("overall") or {}).get("level") or 1)))
    except Exception:
        level = 1

    context_key, context_label = derive_context(data)
    state["last_seen_level"] = level
    state["last_seen_context"] = context_key
    state["updated_at"] = now_iso()

    any_sent = False

    for group_key, group in cfg["groups"].items():
        group_state = state["groups"].setdefault(group_key, default_group_state())

        previous_level = group_state.get("last_notified_level")

        event = determine_event(
            level,
            context_key,
            group_state,
            group["min_level"],
            force_test=force_test,
        )

        if not event:
            group_state["last_result"] = "no_event"
            continue

        group_state["last_event"] = event

        if not configured(cfg, group):
            group_state["last_result"] = "not_configured"
            group_state["last_error"] = "Destinatários ou serviço de e-mail não configurados."
            continue

        if not enabled and not force_test:
            group_state["last_result"] = "disabled"
            group_state["last_error"] = None
            continue

        subject, body_html, body_text = build_message(
            data,
            level,
            context_key,
            context_label,
            event,
            cfg,
            previous_level=previous_level,
        )
        subject = f"{subject} | {group['label']}"
        group_state["last_attempt_at"] = now_iso()
        ok, message_ids, error = send_email(
            cfg,
            group["recipients"],
            subject,
            body_html,
            body_text,
        )

        if ok:
            any_sent = True
            group_state["last_result"] = "sent"
            group_state["last_error"] = None
            group_state["last_message_id"] = message_ids
            group_state["last_sent_at"] = now_iso()
            if event == "recovery":
                group_state["last_notified_level"] = None
                group_state["last_notified_context"] = None
            else:
                group_state["last_notified_level"] = level
                group_state["last_notified_context"] = context_key
            print(
                f"EMAIL_SENT group={group_key} event={event} "
                f"level={level} recipients={len(group['recipients'])}"
            )
        else:
            group_state["last_result"] = "failed"
            group_state["last_error"] = clean_text(error, 500)
            print(
                f"EMAIL_FAILED group={group_key} event={event} "
                f"level={level} error={group_state['last_error']}"
            )

    update_public_status(data, state, cfg, enabled)
    save_json(STATE_PATH, state)
    save_json(STATUS_PATH, data)

    if not any_sent:
        print(f"EMAIL_NO_SEND level={level} context={context_key}")


if __name__ == "__main__":
    main()
