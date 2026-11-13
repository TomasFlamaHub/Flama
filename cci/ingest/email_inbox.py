"""Descarga de reportes adjuntos desde un buzón de correo dedicado (IMAP, solo lectura).

Uso típico: un correo exclusivo (p.ej. reportes@tunegocio.cl) que recibe los
reportes programados de las plataformas, o al que el equipo reenvía los archivos.
El proceso diario descarga los adjuntos CSV/Excel a data/inbox y los marca como leídos.

Variables de entorno: IMAP_HOST, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER (INBOX),
IMAP_ALLOWED_SENDERS (lista separada por comas; vacío = cualquiera).
"""
from __future__ import annotations

import email
import email.utils
import imaplib
import os
import re
from email.header import decode_header
from pathlib import Path

from .. import config

ALLOWED_EXT = {".csv", ".xlsx", ".xls"}


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for text, enc in decode_header(value):
        parts.append(text.decode(enc or "utf-8", "replace") if isinstance(text, bytes) else text)
    return "".join(parts)


def fetch_attachments(dest: Path | None = None) -> list[str]:
    host, user, pwd = (os.environ.get(k) for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"))
    if not (host and user and pwd):
        return []
    dest = dest or config.DATA_DIR / "inbox"
    dest.mkdir(parents=True, exist_ok=True)
    allowed = {s.strip().lower() for s in os.environ.get("IMAP_ALLOWED_SENDERS", "").split(",") if s.strip()}
    saved = []
    with imaplib.IMAP4_SSL(host) as M:
        M.login(user, pwd)
        M.select(os.environ.get("IMAP_FOLDER", "INBOX"))
        _, data = M.search(None, "UNSEEN")
        for num in data[0].split():
            _, msg_data = M.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            sender = email.utils.parseaddr(msg.get("From"))[1].lower()
            if allowed and sender not in allowed:
                continue
            for part in msg.walk():
                fname = _decode(part.get_filename())
                if not fname or Path(fname).suffix.lower() not in ALLOWED_EXT:
                    continue
                safe = re.sub(r"[^\w.\-áéíóúñÁÉÍÓÚÑ ]", "_", Path(fname).name)
                target = dest / safe
                i = 1
                while target.exists():
                    target = dest / f"{Path(safe).stem}_{i}{Path(safe).suffix}"
                    i += 1
                target.write_bytes(part.get_payload(decode=True))
                saved.append(target.name)
            M.store(num, "+FLAGS", "\\Seen")
    return saved
