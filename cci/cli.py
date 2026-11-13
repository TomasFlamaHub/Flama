"""Línea de comandos del Centro de Control.

  python -m cci.cli init                 crea/actualiza la base
  python -m cci.cli daily                corrida diaria completa (lo que ejecuta el cron)
  python -m cci.cli ingest               solo carga (correo + API + carpeta inbox)
  python -m cci.cli alerts [--date D]    recalcula alertas
  python -m cci.cli report [--date D]    genera el informe IA
  python -m cci.cli ask "pregunta"       pregunta al analista IA
  python -m cci.cli profile archivo      muestra columnas de un reporte para armar su mapeo
  python -m cci.cli demo [--purge]       carga / borra datos de demostración
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from . import alerts, db, kpis, notify, quality


def _date(s: str | None) -> date:
    if s:
        return date.fromisoformat(s)
    from .ingest.normalize import yesterday_business_date
    if kpis.is_demo_data():
        return kpis.data_bounds()[1]
    return yesterday_business_date()


def cmd_ingest(lookback_days: int = 3) -> dict:
    from .ingest import email_inbox, files, justo_api
    from .ingest.normalize import yesterday_business_date
    out = {"email": email_inbox.fetch_attachments()}
    D = yesterday_business_date()
    # Se recargan los últimos días: las plataformas corrigen datos con retraso (Uber: hasta 72 h).
    out["justo_api"] = justo_api.ingest_justo(D - timedelta(days=lookback_days - 1), D)
    out["files"] = files.ingest_inbox()
    return out


def cmd_daily(D: date | None, skip_ai: bool) -> None:
    db.init_db()
    print("1/5 Carga:", json.dumps(cmd_ingest(), ensure_ascii=False, default=str))
    D = D or _date(None)
    issues = quality.run_checks(D)
    print(f"2/5 Calidad de datos {D}: {len(issues)} observaciones")
    al = alerts.generate(D)
    print(f"3/5 Alertas: {sum(a['level'] == 'ROJO' for a in al)} rojas, "
          f"{sum(a['level'] == 'AMARILLO' for a in al)} amarillas, {sum(a['level'] == 'VERDE' for a in al)} verdes")
    report = None
    if not skip_ai:
        try:
            from . import analyst
            report = analyst.daily_report(D)
            print("4/5 Informe IA generado")
        except Exception as exc:
            print(f"4/5 Informe IA no generado: {exc}")
    sent = notify.send_email(*notify.build_email(D, al, report, kpis.is_demo_data()))
    print("5/5 Email enviado" if sent else "5/5 Email no configurado/omitido")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cci")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    d = sub.add_parser("daily"); d.add_argument("--date"); d.add_argument("--skip-ai", action="store_true")
    sub.add_parser("ingest")
    a = sub.add_parser("alerts"); a.add_argument("--date")
    r = sub.add_parser("report"); r.add_argument("--date")
    q = sub.add_parser("ask"); q.add_argument("question")
    p = sub.add_parser("profile"); p.add_argument("path"); p.add_argument("--sheet")
    dm = sub.add_parser("demo"); dm.add_argument("--purge", action="store_true"); dm.add_argument("--end")
    fp = sub.add_parser("factpack"); fp.add_argument("--date")
    args = ap.parse_args(argv)

    if args.cmd != "profile":
        db.init_db()
    if args.cmd == "init":
        print("Base inicializada:", db.engine().url.render_as_string(hide_password=True))
    elif args.cmd == "daily":
        cmd_daily(date.fromisoformat(args.date) if args.date else None, args.skip_ai)
    elif args.cmd == "ingest":
        print(json.dumps(cmd_ingest(), ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "alerts":
        D = _date(args.date)
        quality.run_checks(D)
        for x in alerts.generate(D):
            print(f"[{x['level']}] {x['title']} | {x['magnitude']} | vs {x['comparison']}")
    elif args.cmd == "report":
        from . import analyst
        print(analyst.daily_report(_date(args.date)))
    elif args.cmd == "ask":
        from . import analyst
        print(analyst.ask(args.question)[0])
    elif args.cmd == "profile":
        from .ingest import files
        print(files.profile(Path(args.path), args.sheet))
    elif args.cmd == "demo":
        from . import demo
        if args.purge:
            demo.purge(); print("Datos de demostración eliminados.")
        else:
            end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
            print("Cargado (DEMO):", demo.load(end))
            D = kpis.data_bounds()[1]
            quality.run_checks(D)
            print(f"Alertas demo {D}: {len(alerts.generate(D))}")
    elif args.cmd == "factpack":
        from . import factpack
        print(json.dumps(factpack.build(_date(args.date)), ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
