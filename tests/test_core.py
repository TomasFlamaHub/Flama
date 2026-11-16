from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from cci import db, kpis
from cci.ingest import loader
from cci.ingest.normalize import business_date, to_number


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    db.init_db()


def test_business_day_cutoff():
    ts = pd.Series(pd.to_datetime(["2026-11-12 00:40", "2026-11-12 05:10", "2026-11-12 23:59"]))
    assert list(business_date(ts)) == [date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 12)]


def test_to_number_chilean_format():
    s = to_number(pd.Series(["$ 12.345", "1.234.567", "", "-500"]), decimal=",", thousands=".")
    assert s.iloc[0] == 12345 and s.iloc[1] == 1234567 and pd.isna(s.iloc[2]) and s.iloc[3] == -500


def test_orders_upsert_is_idempotent():
    df = pd.DataFrame({"order_id": ["A1", "A2", "A2"], "store": ["KOBO1", "KOBO2", "KOBO2"],
                       "created_at": pd.to_datetime(["2026-11-10 13:00"] * 3), "status": ["completed"] * 3,
                       "gross_amount": [1000.0, 2000.0, 2500.0]})
    r1 = loader.load_orders(df, "rappi", "t1", "test")
    assert r1.inserted == 2 and r1.updated == 0  # duplicado dentro del archivo -> se queda la última versión
    r2 = loader.load_orders(df, "rappi", "t2", "test")
    assert r2.inserted == 0 and r2.updated == 2
    n = db.read_df("SELECT COUNT(*) n, SUM(gross_amount) s FROM orders WHERE source='test'")
    assert n.loc[0, "n"] == 2 and n.loc[0, "s"] == 3500


def test_unknown_store_is_rejected_and_reported():
    df = pd.DataFrame({"order_id": ["X"], "store": ["TIENDA-QUE-NO-EXISTE"],
                       "created_at": pd.to_datetime(["2026-11-10 13:00"]), "gross_amount": [1.0]})
    r = loader.load_orders(df, "ubereats", "t3", "test")
    assert r.rejected == 1 and any(i["check_name"] == "sucursal_desconocida" for i in r.issues)


def test_projection_uses_weekday_pattern():
    # Lunes a jueves pesan 1, viernes a domingo pesan 3 -> total semana = 13 unidades de peso
    w = {0: 1, 1: 1, 2: 1, 3: 1, 4: 3, 5: 3, 6: 3}
    start = date(2026, 11, 9)
    elapsed = [start + timedelta(days=i) for i in range(4)]
    period = [start + timedelta(days=i) for i in range(7)]
    proj, method = kpis.project(400.0, elapsed, period, w)
    assert method == "patrón día de semana" and proj == pytest.approx(1300.0)
    proj_lin, m2 = kpis.project(400.0, elapsed, period, None)
    assert m2 == "lineal" and proj_lin == pytest.approx(700.0)


def test_decomposition_adds_up():
    cur = {"sales_s": 900.0, "orders_s": 90.0, "sessions": 1000.0}
    base = {"sales_s": 1000.0, "orders_s": 100.0, "sessions": 1250.0}
    d = kpis.decompose(cur, base)
    assert d["sesiones"] + d["conversion"] + d["ticket"] == pytest.approx(d["delta"])
    assert d["sesiones"] < 0 and d["conversion"] > 0


def test_periods_homogeneous():
    P = kpis.Periods(date(2026, 11, 12))  # jueves
    assert P.wtd == (date(2026, 11, 9), date(2026, 11, 12))
    assert P.wtd_prev == (date(2026, 11, 2), date(2026, 11, 5))
    assert P.prev_mtd == (date(2026, 10, 1), date(2026, 10, 12))
    P31 = kpis.Periods(date(2026, 3, 31))
    assert P31.prev_mtd == (date(2026, 2, 1), date(2026, 2, 28))  # recorta al largo del mes anterior


def test_demo_end_to_end():
    from cci import alerts, demo, factpack, quality
    demo.load(date(2026, 11, 12), days=60)
    D = date(2026, 11, 12)
    quality.run_checks(D)
    al = alerts.generate(D)
    codes = {a["code"] for a in al}
    assert "open_time_bajo" in codes and "resarcimientos_alza" in codes
    fp = factpack.build(D)
    assert fp["es_demo"] is True and fp["PROYECCION_semana"][0]["sales_proj"] > 0
    demo.purge()
    assert db.read_df("SELECT COUNT(*) n FROM orders WHERE is_demo").loc[0, "n"] == 0


def test_template_csv_ingestion(tmp_path):
    from cci.ingest import files
    inbox, done = tmp_path / "inbox", tmp_path / "done"
    inbox.mkdir()
    (inbox / "plantilla_ordenes_test.csv").write_text(
        "plataforma,id_orden,tienda,fecha_hora,estado,venta_bruta,descuento_local,descuento_plataforma,"
        "comision,otros_cargos,liquidacion,motivo_cancelacion\n"
        "justo,T-1,KOBO3,2026-11-11 20:15,completada,15990,0,0,800,0,15190,\n"
        "rappi,T-2,Kobo 4,2026-11-12 01:30,cancelada,9990,0,0,,,,Sin repartidor\n", encoding="utf-8")
    out = files.ingest_inbox(inbox, done)
    assert out[0]["status"] == "ok", out
    o = db.read_df("SELECT platform_id, branch_id, business_date, status FROM orders WHERE order_id LIKE 'T-%' "
                   "ORDER BY order_id")
    assert list(o["branch_id"]) == ["KOBO3", "KOBO4"] and list(o["status"]) == ["completed", "cancelled"]
    assert str(o.loc[1, "business_date"])[:10] == "2026-11-11"  # 01:30 cuenta para el día anterior
    # reprocesar el mismo archivo no duplica
    (inbox / "plantilla_ordenes_test.csv").write_bytes((done / __import__("datetime").date.today().isoformat()
                                                        / "plantilla_ordenes_test.csv").read_bytes())
    assert files.ingest_inbox(inbox, done)[0]["status"].startswith("skipped")


def test_brands_are_separated():
    from cci import config, demo, factpack
    demo.load(date(2026, 11, 12), days=40)
    D = date(2026, 11, 12)
    crust = kpis.load_panel(D - timedelta(days=7), D, brand="crust")
    assert set(crust["branch_id"]) == {"CRUST1", "CRUST2"} and set(crust["platform_id"]) == {"ubereats"}
    kobo = kpis.load_panel(D - timedelta(days=7), D, brand="kobo")
    assert set(kobo["branch_id"]) == set(config.branches_of("kobo"))
    fp = factpack.build(D, "crust")
    assert fp["marca"] == "The Crust" and fp["plataformas_de_la_marca"] == ["Uber Eats"]
    demo.purge()
