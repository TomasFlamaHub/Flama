"""Modelo de datos y acceso a la base (SQLAlchemy Core: SQLite local / Postgres en producción).

Principios:
  * Una fila por orden y plataforma: PK (platform_id, order_id) -> imposible duplicar ventas.
  * Cargas idempotentes: se hace UPSERT; reprocesar un archivo no duplica nada.
  * Trazabilidad: cada fila guarda source (archivo/API), run_id y updated_at.
  * Datos de demostración siempre marcados con is_demo = true.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Integer, MetaData, String, Table, Text,
    create_engine, select, text,
)
from sqlalchemy.engine import Engine

from . import config

metadata = MetaData()

branch = Table(
    "branch", metadata,
    Column("branch_id", String(16), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("brand_id", String(30)),
    Column("active", Boolean, default=True),
)

platform = Table(
    "platform", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("name", String(60), nullable=False),
)

store_map = Table(
    "store_map", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("external_store_id", String(120), primary_key=True),
    Column("branch_id", String(16), nullable=False),
)

product = Table(
    "product", metadata,
    Column("product_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False, unique=True),
    Column("category", String(120)),
)

product_alias = Table(  # el mismo producto se llama distinto en cada plataforma
    "product_alias", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("raw_name", String(300), primary_key=True),
    Column("product_id", Integer, nullable=False),
)

orders = Table(
    "orders", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("order_id", String(120), primary_key=True),
    Column("branch_id", String(16), nullable=False, index=True),
    Column("created_at", DateTime, nullable=False),        # hora local del negocio
    Column("business_date", Date, nullable=False, index=True),
    Column("status", String(20), nullable=False),          # completed | cancelled | rejected
    Column("gross_amount", Float, nullable=False),         # venta bruta (precio de menú)
    Column("discount_merchant", Float, default=0.0),       # descuentos que paga el local
    Column("discount_platform", Float, default=0.0),       # descuentos que paga la plataforma
    Column("commission", Float),                           # comisión cobrada (si viene en el reporte)
    Column("other_fees", Float),                           # otros cargos (marketing, servicio, etc.)
    Column("payout_amount", Float),                        # liquidación al local
    Column("payout_is_estimated", Boolean, default=False), # True = calculada con % contractual
    Column("cancel_reason", String(300)),
    Column("is_demo", Boolean, default=False),
    Column("source", String(300)),
    Column("run_id", String(40)),
    Column("updated_at", DateTime),
)

order_items = Table(
    "order_items", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("order_id", String(120), primary_key=True),
    Column("line_no", Integer, primary_key=True),
    Column("product_raw", String(300), nullable=False),
    Column("product_id", Integer),
    Column("quantity", Float, nullable=False),
    Column("gross_amount", Float, nullable=False),
    Column("is_demo", Boolean, default=False),
    Column("source", String(300)),
    Column("run_id", String(40)),
)

adjustments = Table(  # resarcimientos / reembolsos / contracargos (monto positivo = dinero perdido)
    "adjustments", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("adjustment_id", String(160), primary_key=True),
    Column("order_id", String(120)),
    Column("branch_id", String(16), nullable=False, index=True),
    Column("business_date", Date, nullable=False, index=True),
    Column("amount", Float, nullable=False),
    Column("kind", String(40)),
    Column("reason", String(300)),
    Column("is_demo", Boolean, default=False),
    Column("source", String(300)),
    Column("run_id", String(40)),
)

store_day = Table(  # métricas operativas diarias por sucursal x plataforma
    "store_day", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("branch_id", String(16), primary_key=True),
    Column("business_date", Date, primary_key=True),
    Column("scheduled_minutes", Float),   # horario en que DEBERÍA estar abierto
    Column("open_minutes", Float),        # Open Time real
    Column("offline_minutes", Float),     # minutos sin disponibilidad informados por la plataforma
    Column("sessions", Float),            # visitas/sesiones según definición de la plataforma
    Column("is_demo", Boolean, default=False),
    Column("source", String(300)),
    Column("run_id", String(40)),
)

funnel_period = Table(  # embudo de conversión por período (las plataformas no lo dan diario)
    "funnel_period", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("branch_id", String(16), primary_key=True),   # ALL_<marca> = todas las tiendas juntas
    Column("start_date", Date, primary_key=True),
    Column("end_date", Date, primary_key=True),
    Column("store_views", Float),      # tienda visualizada / impresiones
    Column("menu_views", Float),       # vieron el menú
    Column("add_to_cart", Float),
    Column("orders", Float),
    Column("conversion_reported", Float),  # tasa que muestra el portal, si solo existe el %
    Column("is_demo", Boolean, default=False),
    Column("source", String(300)),
    Column("run_id", String(40)),
)

period_summary = Table(  # totales por tienda y período (ranking de tiendas del portal)
    "period_summary", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("branch_id", String(16), primary_key=True),
    Column("start_date", Date, primary_key=True),
    Column("end_date", Date, primary_key=True),
    Column("sales", Float), Column("orders", Float), Column("items", Float),
    Column("sales_delta_pct", Float),
    Column("source", String(300)), Column("run_id", String(40)),
)

weekly_summary = Table(  # venta semanal por tienda (resumen del portal, sin detalle por pedido)
    "weekly_summary", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("branch_id", String(16), primary_key=True),
    Column("week_start", Date, primary_key=True),
    Column("sales", Float), Column("orders", Float),
    Column("sales_delivery", Float), Column("sales_pickup", Float),
    Column("source", String(300)), Column("run_id", String(40)),
)

product_period = Table(  # ranking de productos por período (resumen del portal)
    "product_period", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("branch_id", String(16), primary_key=True),
    Column("start_date", Date, primary_key=True),
    Column("end_date", Date, primary_key=True),
    Column("product", String(300), primary_key=True),
    Column("sales", Float), Column("units", Float),
    Column("sales_delta_pct", Float), Column("units_delta_pct", Float),
    Column("source", String(300)), Column("run_id", String(40)),
)

hourly_period = Table(  # ventas por día de semana x hora acumuladas en un período
    "hourly_period", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("branch_id", String(16), primary_key=True),
    Column("start_date", Date, primary_key=True),
    Column("end_date", Date, primary_key=True),
    Column("dow", Integer, primary_key=True),     # 0 = lunes
    Column("hour", Integer, primary_key=True),
    Column("sales", Float), Column("orders", Float),
    Column("source", String(300)), Column("run_id", String(40)),
)

quality_events = Table(  # reclamos, errores de pedido, cancelaciones con motivo
    "quality_events", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("event_id", String(160), primary_key=True),
    Column("kind", String(30)),        # reclamo | reclamo_producto | cancelacion | error_pedido
    Column("order_id", String(120)),
    Column("branch_id", String(16), index=True),
    Column("business_date", Date, index=True),
    Column("reason", String(200)),
    Column("detail", Text),
    Column("product", String(300)),
    Column("amount", Float),
    Column("is_demo", Boolean, default=False),
    Column("source", String(300)),
    Column("run_id", String(40)),
)

reviews = Table(
    "reviews", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("review_id", String(160), primary_key=True),
    Column("order_id", String(120)),
    Column("branch_id", String(16), index=True),
    Column("business_date", Date, index=True),
    Column("rating", Float),
    Column("reason", String(300)),
    Column("comment", Text),
    Column("is_demo", Boolean, default=False),
    Column("source", String(300)),
    Column("run_id", String(40)),
)

order_ops = Table(  # tiempos operativos por pedido
    "order_ops", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("order_id", String(120), primary_key=True),
    Column("branch_id", String(16), index=True),
    Column("business_date", Date, index=True),
    Column("prep_expected_min", Float),
    Column("courier_wait_min", Float),
    Column("prep_real_min", Float),
    Column("total_min", Float),
    Column("source", String(300)),
    Column("run_id", String(40)),
)

costs = Table(  # costos que no son por pedido: publicidad, ajustes, cargos
    "costs", metadata,
    Column("platform_id", String(16), primary_key=True),
    Column("cost_id", String(160), primary_key=True),
    Column("branch_id", String(16), index=True),
    Column("business_date", Date, index=True),
    Column("kind", String(40)),        # ads | ajuste | otro   (monto positivo = costo)
    Column("amount", Float),
    Column("description", Text),
    Column("is_demo", Boolean, default=False),
    Column("source", String(300)),
    Column("run_id", String(40)),
)

ingestion_run = Table(
    "ingestion_run", metadata,
    Column("run_id", String(40), primary_key=True),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime),
    Column("platform_id", String(16)),
    Column("source_type", String(30)),      # api | file | email | demo
    Column("source_name", String(300)),
    Column("file_hash", String(64), index=True),
    Column("dataset", String(30)),
    Column("rows_read", Integer, default=0),
    Column("rows_inserted", Integer, default=0),
    Column("rows_updated", Integer, default=0),
    Column("rows_rejected", Integer, default=0),
    Column("status", String(20)),           # ok | warning | error | skipped
    Column("message", Text),
)

dq_issue = Table(
    "dq_issue", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("detected_at", DateTime, nullable=False),
    Column("run_id", String(40)),
    Column("severity", String(10)),         # error | warning | info
    Column("platform_id", String(16)),
    Column("branch_id", String(16)),
    Column("business_date", Date),
    Column("check_name", String(80)),
    Column("detail", Text),
)

alert = Table(
    "alert", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("generated_at", DateTime, nullable=False),
    Column("business_date", Date, nullable=False, index=True),
    Column("level", String(10), nullable=False),   # ROJO | AMARILLO | VERDE
    Column("code", String(40), nullable=False),
    Column("brand_id", String(30)),
    Column("branch_id", String(16)),
    Column("platform_id", String(16)),
    Column("title", String(300)),
    Column("what", Text),
    Column("magnitude", String(200)),
    Column("comparison", String(300)),
    Column("recommendation", Text),
    Column("is_demo", Boolean, default=False),
)

ai_report = Table(
    "ai_report", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("business_date", Date, nullable=False, index=True),
    Column("brand_id", String(30)),
    Column("generated_at", DateTime, nullable=False),
    Column("model", String(60)),
    Column("content_md", Text),
    Column("is_demo", Boolean, default=False),
)

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(config.database_url(), future=True, pool_pre_ping=True)
    return _engine


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def init_db() -> None:
    """Crea tablas y sincroniza sucursales/plataformas/mapeo de tiendas desde settings.yaml."""
    eng = engine()
    metadata.create_all(eng)
    s = config.settings()
    upsert(branch, [{"branch_id": b["id"], "name": b["name"], "brand_id": config.brand_of(b["id"]), "active": True}
                    for b in s["branches"]])
    upsert(platform, [{"platform_id": k, "name": v["name"]} for k, v in s["platforms"].items()])
    maps = [m for m in s.get("store_mapping", []) if m.get("external_store_id") not in (None, "", "COMPLETAR")]
    if maps:
        upsert(store_map, [{"platform_id": m["platform"], "external_store_id": str(m["external_store_id"]),
                            "branch_id": m["branch_id"]} for m in maps])


def _pk_cols(table: Table) -> list[str]:
    return [c.name for c in table.primary_key.columns]


def upsert(table: Table, rows: list[dict], chunk: int = 500) -> tuple[int, int]:
    """INSERT ... ON CONFLICT DO UPDATE. Devuelve (insertadas, actualizadas)."""
    if not rows:
        return 0, 0
    eng = engine()
    pk = _pk_cols(table)
    dialect = eng.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

    # Cuántas ya existían (para el registro de la carga)
    existing = 0
    with eng.begin() as conn:
        if len(pk) and all(k in rows[0] for k in pk):
            keys = {tuple(r[k] for k in pk) for r in rows}
            if len(pk) == 1:
                col = table.c[pk[0]]
                vals = [k[0] for k in keys]
                for i in range(0, len(vals), chunk):
                    existing += len(conn.execute(select(col).where(col.in_(vals[i:i + chunk]))).all())
            else:
                # Filtrar por la columna más selectiva de la PK y comparar en memoria
                idx = max(range(len(pk)), key=lambda j: len({k[j] for k in keys}))
                first = table.c[pk[idx]]
                firsts = list({k[idx] for k in keys})
                found = set()
                for i in range(0, len(firsts), chunk):
                    res = conn.execute(select(*[table.c[k] for k in pk]).where(first.in_(firsts[i:i + chunk])))
                    found.update(tuple(r) for r in res)
                existing = len(keys & found)
        for i in range(0, len(rows), chunk):
            part = rows[i:i + chunk]
            stmt = dialect_insert(table).values(part)
            update_cols = {c: stmt.excluded[c] for c in part[0].keys() if c not in pk}
            stmt = stmt.on_conflict_do_update(index_elements=pk, set_=update_cols) if update_cols \
                else stmt.on_conflict_do_nothing(index_elements=pk)
            conn.execute(stmt)
    unique = len({tuple(r.get(k) for k in pk) for r in rows})
    return unique - existing, existing


def read_df(sql: str, params: dict | None = None) -> pd.DataFrame:
    with engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def insert_rows(table: Table, rows: list[dict]) -> None:
    if rows:
        with engine().begin() as conn:
            conn.execute(table.insert(), rows)


def execute(sql: str, params: dict | None = None) -> None:
    with engine().begin() as conn:
        conn.execute(text(sql), params or {})
