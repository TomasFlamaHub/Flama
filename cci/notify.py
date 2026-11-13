"""Envío del resumen diario por email (SMTP). WhatsApp: ver docs/02_arquitectura.md (fase 2)."""
from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config

ICON = {"ROJO": "🔴", "AMARILLO": "🟡", "VERDE": "🟢"}


def build_email(D, alerts: list[dict], report_md: str | None, demo: bool) -> tuple[str, str]:
    cfg = config.settings()
    levels = set(cfg["notifications"]["email"].get("send_levels", ["ROJO", "AMARILLO"]))
    reds = sum(a["level"] == "ROJO" for a in alerts)
    subject = f"{'[DEMO] ' if demo else ''}Control diario {D} · {reds} alertas rojas"
    lines = [f"<h2>{cfg['business']['name']} · Control diario {D}</h2>"]
    if demo:
        lines.append("<p><b>DATOS DE DEMOSTRACIÓN – no son datos reales.</b></p>")
    url = os.environ.get("DASHBOARD_URL")
    if url:
        lines.append(f'<p><a href="{url}">Abrir el panel</a></p>')
    lines.append("<h3>Alertas</h3><ul>")
    for a in alerts:
        if a["level"] in levels:
            lines.append(f"<li>{ICON[a['level']]} <b>{a['title']}</b> — {a['what']}<br>"
                         f"<i>Acción:</i> {a['recommendation']}</li>")
    lines.append("</ul>")
    if report_md:
        lines.append("<h3>Informe del analista</h3><pre style='white-space:pre-wrap;font-family:inherit'>"
                     + report_md.replace("<", "&lt;") + "</pre>")
    return subject, "\n".join(lines)


def send_email(subject: str, html: str) -> bool:
    cfg = config.settings()["notifications"]["email"]
    host, user, pwd = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    if not (cfg.get("enabled") and cfg.get("to") and host and user and pwd):
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_FROM", user), ", ".join(cfg["to"])
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
    return True
