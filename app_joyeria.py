#!/usr/bin/env python3
"""
SISTEMA DE GESTIÓN, COTIZACIÓN Y FACTURACIÓN
MANILLAS CON BALINES DIAMANTADOS EN ORO 18K
- Cotizador de acceso directo sin login
- Panel Administrativo protegido por correo y contraseña
- Precio de venta final editable / redondeable con cálculo real de ganancia
- Resumen de Cuenta Bancaria, Ganancias Reales y Valoración de Inventario
- Servidor Multihilo rápido + Auto-Persistencia contra reinicios de Render
Ejecución: python app_joyeria.py
"""

import http.server
import socketserver
import json
import sqlite3
import urllib.parse
import urllib.request
import webbrowser
import hashlib
import secrets
import base64
import os
import sys
import tempfile
from datetime import datetime

PORT = int(os.environ.get("PORT", 8000))

# Opcional: Variables para persistencia automática en GitHub si se configuran en Render
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "hrubioitanare/joyeria-app").strip()
GITHUB_BACKUP_PATH = "backup_joyeria_db.json"


def get_db_path():
    local_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(local_dir, "joyeria_balines.db")
    try:
        conn = sqlite3.connect(local_path, timeout=20)
        conn.execute("CREATE TABLE IF NOT EXISTS _test (id INT)")
        conn.execute("DROP TABLE _test")
        conn.close()
        return local_path
    except Exception:
        return os.path.join(tempfile.gettempdir(), "joyeria_balines.db")


DB_PATH = get_db_path()


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


INITIAL_BEADS = [
    ("3 mm", 12400.0, 25.0, 15500.0, 15.0, 14260.0, 285200.0, 0),
    ("4 mm", 22400.0, 20.0, 26880.0, 15.0, 25760.0, 515200.0, 0),
    ("5 mm", 31500.0, 20.0, 37800.0, 15.0, 36225.0, 724500.0, 0),
    ("6 mm", 51000.0, 15.0, 58650.0, 10.0, 56100.0, 1122000.0, 0),
    ("7 mm", 69800.0, 20.0, 83760.0, 10.0, 76780.0, 1535600.0, 0),
    ("8 mm", 93900.0, 20.0, 112680.0, 10.0, 103290.0, 2065800.0, 0),
]


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    # 1. Tabla de Usuarios
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password_hash TEXT,
            name TEXT,
            role TEXT
        )
    """)

    # 2. Tabla de Sesiones
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER,
            role TEXT,
            created_at TEXT
        )
    """)

    # 3. Tabla de inventario de balines
    c.execute("""
        CREATE TABLE IF NOT EXISTS beads_inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            size TEXT UNIQUE,
            cost REAL,
            gain_unit_pct REAL,
            price_unit REAL,
            gain_x20_pct REAL,
            price_unit_x20 REAL,
            price_total_x20 REAL,
            stock INTEGER DEFAULT 0
        )
    """)

    # 4. Tabla de flujo de caja (Ingresos y Salidas)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cash_flow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            type TEXT,
            category TEXT,
            description TEXT,
            amount REAL,
            invoice_id INTEGER NULL
        )
    """)

    # 5. Tabla de facturas
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT UNIQUE,
            date TEXT,
            customer_name TEXT,
            customer_phone TEXT,
            customer_id_doc TEXT,
            subtotal_beads REAL,
            bracelet_surcharge REAL,
            surcharge_rule TEXT,
            suggested_amount REAL DEFAULT 0,
            discount_amount REAL DEFAULT 0,
            total_amount REAL,
            cost_amount REAL,
            profit_amount REAL,
            beads_details TEXT,
            notes TEXT
        )
    """)

    # Migración segura de columnas nuevas si la tabla ya existía
    existing_cols = [row["name"] for row in c.execute("PRAGMA table_info(invoices)").fetchall()]
    if "suggested_amount" not in existing_cols:
        c.execute("ALTER TABLE invoices ADD COLUMN suggested_amount REAL DEFAULT 0")
    if "discount_amount" not in existing_cols:
        c.execute("ALTER TABLE invoices ADD COLUMN discount_amount REAL DEFAULT 0")
    if "beads_details" not in existing_cols:
        c.execute("ALTER TABLE invoices ADD COLUMN beads_details TEXT DEFAULT '{}'")

    # 6. Tabla de configuración / metadatos de sincronización
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # Insertar usuarios por defecto si no existen
    c.execute("SELECT COUNT(*) as cnt FROM users")
    if c.fetchone()["cnt"] == 0:
        c.execute("""
            INSERT INTO users (email, password_hash, name, role)
            VALUES (?, ?, ?, ?)
        """, ("admin@joyeria.com", hash_password("admin123"), "Administrador Principal", "ADMIN"))
        c.execute("""
            INSERT INTO users (email, password_hash, name, role)
            VALUES (?, ?, ?, ?)
        """, ("vendedor@joyeria.com", hash_password("cotizar123"), "Vendedor / Cotizador", "COTIZADOR"))

    # Insertar balines iniciales si no existen
    for b in INITIAL_BEADS:
        c.execute("""
            INSERT OR IGNORE INTO beads_inventory 
            (size, cost, gain_unit_pct, price_unit, gain_x20_pct, price_unit_x20, price_total_x20, stock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, b)

    conn.commit()
    conn.close()

    # Intentar restaurar desde GitHub Cloud si el contenedor de Render acaba de iniciar desde cero
    try_restore_from_github_on_boot()


def export_full_backup():
    conn = get_db()
    c = conn.cursor()
    beads = [dict(r) for r in c.execute("SELECT * FROM beads_inventory ORDER BY id ASC").fetchall()]
    cash_flow = [dict(r) for r in c.execute("SELECT * FROM cash_flow ORDER BY id ASC").fetchall()]
    invoices = [dict(r) for r in c.execute("SELECT * FROM invoices ORDER BY id ASC").fetchall()]
    users = [dict(r) for r in c.execute("SELECT * FROM users ORDER BY id ASC").fetchall()]
    meta_row = c.execute("SELECT value FROM app_meta WHERE key = 'updated_at'").fetchone()
    updated_at = meta_row["value"] if meta_row else ""
    conn.close()

    return {
        "updated_at": updated_at,
        "beads_inventory": beads,
        "cash_flow": cash_flow,
        "invoices": invoices,
        "users": users
    }


def touch_updated_at(conn=None):
    close_after = False
    if conn is None:
        conn = get_db()
        close_after = True
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('updated_at', ?)", (now_iso,))
    if close_after:
        conn.commit()
        conn.close()
    return now_iso


def import_full_backup(data):
    if not data or not isinstance(data, dict):
        return False
    conn = get_db()
    c = conn.cursor()

    if "beads_inventory" in data and isinstance(data["beads_inventory"], list):
        for b in data["beads_inventory"]:
            c.execute("""
                INSERT INTO beads_inventory (size, cost, gain_unit_pct, price_unit, gain_x20_pct, price_unit_x20, price_total_x20, stock)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(size) DO UPDATE SET
                    cost=excluded.cost,
                    gain_unit_pct=excluded.gain_unit_pct,
                    price_unit=excluded.price_unit,
                    gain_x20_pct=excluded.gain_x20_pct,
                    price_unit_x20=excluded.price_unit_x20,
                    price_total_x20=excluded.price_total_x20,
                    stock=excluded.stock
            """, (
                b.get("size"), b.get("cost", 0), b.get("gain_unit_pct", 0),
                b.get("price_unit", 0), b.get("gain_x20_pct", 0),
                b.get("price_unit_x20", 0), b.get("price_total_x20", 0),
                b.get("stock", 0)
            ))

    if "cash_flow" in data and isinstance(data["cash_flow"], list):
        c.execute("DELETE FROM cash_flow")
        for cf in data["cash_flow"]:
            c.execute("""
                INSERT INTO cash_flow (id, date, type, category, description, amount, invoice_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                cf.get("id"), cf.get("date"), cf.get("type"),
                cf.get("category"), cf.get("description"),
                cf.get("amount", 0), cf.get("invoice_id")
            ))

    if "invoices" in data and isinstance(data["invoices"], list):
        c.execute("DELETE FROM invoices")
        for inv in data["invoices"]:
            c.execute("""
                INSERT INTO invoices (
                    id, invoice_number, date, customer_name, customer_phone, customer_id_doc,
                    subtotal_beads, bracelet_surcharge, surcharge_rule, suggested_amount,
                    discount_amount, total_amount, cost_amount, profit_amount, beads_details, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                inv.get("id"), inv.get("invoice_number"), inv.get("date"),
                inv.get("customer_name"), inv.get("customer_phone", ""),
                inv.get("customer_id_doc", ""), inv.get("subtotal_beads", 0),
                inv.get("bracelet_surcharge", 0), inv.get("surcharge_rule", ""),
                inv.get("suggested_amount", inv.get("total_amount", 0)),
                inv.get("discount_amount", 0), inv.get("total_amount", 0),
                inv.get("cost_amount", 0), inv.get("profit_amount", 0),
                inv.get("beads_details", "{}"), inv.get("notes", "")
            ))

    if "users" in data and isinstance(data["users"], list) and len(data["users"]) > 0:
        for u in data["users"]:
            if u.get("email") and u.get("password_hash"):
                c.execute("""
                    INSERT OR IGNORE INTO users (email, password_hash, name, role)
                    VALUES (?, ?, ?, ?)
                """, (u.get("email"), u.get("password_hash"), u.get("name", "Usuario"), u.get("role", "ADMIN")))

    ts = data.get("updated_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('updated_at', ?)", (ts,))
    conn.commit()
    conn.close()
    return True


def sync_to_github_cloud():
    """Si GITHUB_TOKEN está configurado, guarda el respaldo JSON en el repositorio automáticamente."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    try:
        backup_data = export_full_backup()
        raw_json = json.dumps(backup_data, ensure_ascii=False, indent=2).encode("utf-8")
        content_b64 = base64.b64encode(raw_json).decode("ascii")

        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_BACKUP_PATH}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "JoyeriaApp-CloudSync"
        }

        sha = None
        try:
            req_get = urllib.request.Request(api_url, headers=headers, method="GET")
            with urllib.request.urlopen(req_get, timeout=8) as resp:
                existing = json.loads(resp.read().decode("utf-8"))
                sha = existing.get("sha")
        except Exception:
            pass

        body = {
            "message": f"Auto-backup base de datos {backup_data.get('updated_at', '')}",
            "content": content_b64
        }
        if sha:
            body["sha"] = sha

        req_put = urllib.request.Request(
            api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="PUT"
        )
        urllib.request.urlopen(req_put, timeout=10).close()
    except Exception as e:
        print("Aviso sync GitHub:", e)


def try_restore_from_github_on_boot():
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    try:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_BACKUP_PATH}"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "JoyeriaApp-CloudSync"
        }
        req = urllib.request.Request(api_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            content_b64 = payload.get("content", "")
            if content_b64:
                decoded = base64.b64decode(content_b64).decode("utf-8")
                data = json.loads(decoded)
                import_full_backup(data)
                print("✅ Base de datos restaurada desde respaldo en GitHub.")
    except Exception:
        pass


init_db()


def authenticate_request(headers):
    auth_header = headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    if not token:
        return None

    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT s.token, u.id, u.email, u.name, u.role 
        FROM sessions s 
        JOIN users u ON s.user_id = u.id 
        WHERE s.token = ?
    """, (token,))
    user = c.fetchone()
    conn.close()
    return dict(user) if user else None


def get_financial_and_inventory_summary():
    conn = get_db()
    c = conn.cursor()

    beads = [dict(r) for r in c.execute("SELECT * FROM beads_inventory").fetchall()]
    total_stock_units = sum(int(b["stock"] or 0) for b in beads)
    inventory_cost_value = sum((int(b["stock"] or 0) * float(b["cost"] or 0)) for b in beads)
    inventory_sale_value = sum((int(b["stock"] or 0) * float(b["price_unit"] or 0)) for b in beads)
    inventory_potential_profit = inventory_sale_value - inventory_cost_value

    transactions = [dict(r) for r in c.execute("SELECT * FROM cash_flow").fetchall()]
    total_income = sum(float(t["amount"] or 0) for t in transactions if t["type"] == "INGRESO")
    total_expense = sum(float(t["amount"] or 0) for t in transactions if t["type"] == "SALIDA")
    bank_balance = total_income - total_expense

    invoices = [dict(r) for r in c.execute("SELECT * FROM invoices").fetchall()]
    total_invoiced_sales = sum(float(i["total_amount"] or 0) for i in invoices)
    total_realized_profit = sum(float(i["profit_amount"] or 0) for i in invoices)
    total_recovered_cost = sum(float(i["cost_amount"] or 0) for i in invoices)
    invoices_count = len(invoices)

    meta_row = c.execute("SELECT value FROM app_meta WHERE key = 'updated_at'").fetchone()
    updated_at = meta_row["value"] if meta_row else ""

    conn.close()

    return {
        "updated_at": updated_at,
        "total_stock_units": total_stock_units,
        "inventory_cost_value": inventory_cost_value,
        "inventory_sale_value": inventory_sale_value,
        "inventory_potential_profit": inventory_potential_profit,
        "total_income": total_income,
        "total_expense": total_expense,
        "bank_balance": bank_balance,
        "total_invoiced_sales": total_invoiced_sales,
        "total_realized_profit": total_realized_profit,
        "total_recovered_cost": total_recovered_cost,
        "invoices_count": invoices_count
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Joyería Oro & Balines 18K - Cotizador y Gestión</title>
    <style>
        :root {
            --gold-primary: #D4AF37;
            --gold-bright: #F5D061;
            --gold-light: #F9E8A2;
            --gold-dark: #9A7B1C;
            --bg-main: #070A12;
            --bg-card: #111726;
            --bg-card-alt: #192236;
            --bg-input: #0B101B;
            --text-main: #F8FAFC;
            --text-muted: #94A3B8;
            --border-color: #26334D;
            --border-gold: rgba(212, 175, 55, 0.35);
            --success: #10B981;
            --success-bg: rgba(16, 185, 129, 0.12);
            --danger: #EF4444;
            --danger-bg: rgba(239, 68, 68, 0.12);
            --info: #38BDF8;
            --shadow: 0 12px 32px -4px rgba(0, 0, 0, 0.6);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; }
        body {
            background: radial-gradient(circle at top right, #161e31 0%, var(--bg-main) 60%);
            color: var(--text-main);
            line-height: 1.5;
            min-height: 100vh;
            padding-bottom: 60px;
        }

        /* HEADER */
        header {
            background: rgba(17, 23, 38, 0.92);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border-gold);
            padding: 0.9rem 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
        }
        .header-brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .brand-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: linear-gradient(135deg, var(--gold-bright), var(--gold-dark));
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.3rem;
            color: #000;
            font-weight: bold;
            box-shadow: 0 4px 15px rgba(212, 175, 55, 0.3);
        }
        .header-brand h1 {
            font-size: 1.25rem;
            background: linear-gradient(90deg, var(--gold-light), var(--gold-primary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
            letter-spacing: 0.5px;
        }
        .header-brand small {
            display: block;
            color: var(--text-muted);
            font-size: 0.75rem;
        }
        .header-user {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .badge-role {
            padding: 4px 12px;
            border-radius: 20px;
            font-weight: 700;
            font-size: 0.73rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .badge-admin { background: rgba(212, 175, 55, 0.2); color: var(--gold-bright); border: 1px solid var(--gold-primary); }
        .badge-guest { background: rgba(56, 189, 248, 0.15); color: var(--info); border: 1px solid rgba(56, 189, 248, 0.4); }
        .sync-indicator {
            font-size: 0.75rem;
            color: var(--success);
            background: var(--success-bg);
            border: 1px solid rgba(16, 185, 129, 0.3);
            padding: 4px 10px;
            border-radius: 20px;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }

        /* NAVIGATION */
        nav {
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(17, 23, 38, 0.85);
            backdrop-filter: blur(10px);
            padding: 10px 1.5rem;
            border-bottom: 1px solid var(--border-color);
            position: sticky;
            top: 0;
            z-index: 100;
            overflow-x: auto;
            white-space: nowrap;
            -webkit-overflow-scrolling: touch;
        }
        .nav-btn {
            background: transparent;
            color: var(--text-muted);
            border: 1px solid transparent;
            padding: 8px 16px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 0.9rem;
            font-weight: 600;
            transition: all 0.2s;
            flex-shrink: 0;
        }
        .nav-btn:hover { color: var(--gold-light); background: rgba(212, 175, 55, 0.08); border-color: var(--border-gold); }
        .nav-btn.active {
            background: linear-gradient(135deg, var(--gold-bright) 0%, var(--gold-primary) 100%);
            color: #090D16;
            font-weight: 700;
            box-shadow: 0 4px 12px rgba(212, 175, 55, 0.25);
        }

        /* LAYOUT & CARDS */
        .container { max-width: 1260px; margin: 22px auto; padding: 0 16px; }
        .tab-content { display: none; animation: fadeIn 0.2s ease-in-out; }
        .tab-content.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 22px;
            margin-bottom: 20px;
            box-shadow: var(--shadow);
        }
        .card.gold-border { border-color: var(--border-gold); }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 12px;
            flex-wrap: wrap;
            gap: 10px;
        }
        .card-header h2 { font-size: 1.18rem; color: var(--gold-bright); display: flex; align-items: center; gap: 8px; }

        .grid-2 { display: grid; grid-template-columns: 1.05fr 0.95fr; gap: 20px; }
        .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
        .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
        @media (max-width: 960px) { .grid-4 { grid-template-columns: repeat(2, 1fr); } }
        @media (max-width: 820px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
        @media (max-width: 520px) { .grid-4 { grid-template-columns: 1fr; } }

        /* FORMS & BUTTONS */
        .form-group { margin-bottom: 14px; }
        .form-group label {
            display: block;
            margin-bottom: 6px;
            font-size: 0.78rem;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }
        .form-control {
            width: 100%;
            padding: 11px 14px;
            background: var(--bg-input);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            color: #fff;
            font-size: 0.95rem;
            transition: border 0.2s;
        }
        .form-control:focus { outline: none; border-color: var(--gold-primary); box-shadow: 0 0 0 3px rgba(212, 175, 55, 0.15); }

        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 7px;
            padding: 10px 18px;
            border-radius: 10px;
            border: none;
            font-size: 0.92rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-gold {
            background: linear-gradient(135deg, var(--gold-bright) 0%, var(--gold-primary) 100%);
            color: #090D16;
            box-shadow: 0 4px 14px rgba(212, 175, 55, 0.25);
        }
        .btn-gold:hover { filter: brightness(1.08); transform: translateY(-1px); }
        .btn-secondary { background: var(--bg-card-alt); color: var(--text-main); border: 1px solid var(--border-color); }
        .btn-secondary:hover { border-color: var(--gold-primary); color: var(--gold-light); }
        .btn-danger { background: rgba(239, 68, 68, 0.2); color: #FCA5A5; border: 1px solid var(--danger); }
        .btn-danger:hover { background: var(--danger); color: #fff; }
        .btn-sm { padding: 6px 12px; font-size: 0.8rem; border-radius: 8px; }
        .chip-btn {
            background: rgba(212, 175, 55, 0.12);
            color: var(--gold-light);
            border: 1px solid var(--border-gold);
            padding: 5px 10px;
            border-radius: 8px;
            font-size: 0.78rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s;
        }
        .chip-btn:hover { background: var(--gold-primary); color: #000; }

        /* TABLES */
        .table-responsive { overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: 10px; }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th {
            background: var(--bg-card-alt);
            color: var(--gold-light);
            font-size: 0.76rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 12px;
            border-bottom: 1px solid var(--border-color);
        }
        td { padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); font-size: 0.9rem; vertical-align: middle; }
        tr:hover td { background: rgba(255, 255, 255, 0.02); }

        /* STAT CARDS */
        .stat-card {
            background: linear-gradient(145deg, var(--bg-card-alt), var(--bg-card));
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 16px;
            border-left: 4px solid var(--gold-primary);
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.25);
        }
        .stat-card.success-border { border-left-color: var(--success); }
        .stat-card.danger-border { border-left-color: var(--danger); }
        .stat-card.info-border { border-left-color: var(--info); }
        .stat-label { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 700; letter-spacing: 0.4px; }
        .stat-value { font-size: 1.45rem; font-weight: 800; color: #fff; margin-top: 5px; }
        .stat-sub { font-size: 0.78rem; color: var(--text-muted); margin-top: 3px; }

        /* BEAD SELECTOR ROWS */
        .bead-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 14px;
            margin-bottom: 8px;
            background: var(--bg-card-alt);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            gap: 10px;
            transition: border 0.2s;
        }
        .bead-row.selected-row { border-color: var(--gold-primary); background: rgba(212, 175, 55, 0.07); }
        .bead-counter { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
        .bead-counter button {
            width: 36px;
            height: 36px;
            border-radius: 8px;
            border: 1px solid var(--border-color);
            background: var(--bg-main);
            color: var(--gold-bright);
            font-size: 1.2rem;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.15s;
        }
        .bead-counter button:hover { background: var(--gold-primary); color: #000; }
        .bead-counter input {
            width: 52px;
            height: 36px;
            text-align: center;
            background: var(--bg-input);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: #fff;
            font-weight: 800;
            font-size: 1rem;
        }

        .rule-badge {
            background: rgba(56, 189, 248, 0.1);
            border: 1px solid rgba(56, 189, 248, 0.4);
            color: var(--info);
            padding: 10px 14px;
            border-radius: 10px;
            font-size: 0.85rem;
            margin: 12px 0;
        }
        .rule-badge.high-value {
            background: rgba(212, 175, 55, 0.15);
            border-color: var(--gold-primary);
            color: var(--gold-light);
        }

        /* PRICE OVERRIDE BOX */
        .price-override-box {
            background: linear-gradient(135deg, rgba(212, 175, 55, 0.12), rgba(17, 23, 38, 0.9));
            border: 1.5px solid var(--gold-primary);
            border-radius: 12px;
            padding: 14px;
            margin-bottom: 14px;
        }
        .price-input-large {
            font-size: 1.35rem !important;
            font-weight: 800 !important;
            color: var(--gold-bright) !important;
            text-align: right;
            background: #070A12 !important;
        }

        /* MODALS */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0, 0, 0, 0.82);
            backdrop-filter: blur(5px);
            z-index: 1000;
            justify-content: center;
            align-items: center;
            padding: 15px;
        }
        .modal-overlay.active { display: flex; }
        .modal-content {
            background: #fff;
            color: #1a1a1a;
            width: 100%;
            max-width: 580px;
            max-height: 92vh;
            overflow-y: auto;
            border-radius: 14px;
            padding: 24px;
            box-shadow: 0 25px 50px rgba(0,0,0,0.7);
        }
        .modal-dark {
            background: var(--bg-card);
            color: var(--text-main);
            border: 1px solid var(--gold-primary);
        }

        /* PRINT INVOICE */
        .inv-header { text-align: center; border-bottom: 2px dashed #cbd5e1; padding-bottom: 12px; margin-bottom: 12px; }
        .inv-table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.88rem; }
        .inv-table th { background: #f1f5f9; color: #334155; padding: 8px; border-bottom: 2px solid #cbd5e1; }
        .inv-table td { padding: 8px; border-bottom: 1px solid #e2e8f0; color: #1e293b; }
        .inv-totals { margin-left: auto; width: 70%; margin-top: 10px; }
        .inv-totals-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 0.9rem; }
        .inv-totals-row.grand-total { border-top: 2px solid #1e293b; padding-top: 8px; font-size: 1.15rem; font-weight: 800; color: #b45309; }

        @media print {
            body * { visibility: hidden; }
            #invoice-modal, #invoice-modal .modal-content, #invoice-modal .modal-content * { visibility: visible; }
            #invoice-modal { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: white; padding: 0; }
            .no-print { display: none !important; }
        }
    </style>
</head>
<body>

    <!-- ==================== ENCABEZADO ==================== -->
    <header>
        <div class="header-brand">
            <div class="brand-icon">👑</div>
            <div>
                <h1>JOYERÍA ORO & BALINES 18K</h1>
                <small>Cotizador Inteligente, Inventario & Facturación</small>
            </div>
        </div>
        <div class="header-user">
            <span id="sync-badge" class="sync-indicator" title="Tus datos están respaldados automáticamente">☁️ Datos Sincronizados</span>
            <span id="user-display-badge" class="badge-role badge-guest">MODO COTIZADOR LIBRE</span>
            <span id="user-display-email" style="color: var(--text-muted); font-size: 0.82rem; display:none;"></span>
            <button id="btn-open-login" class="btn btn-gold btn-sm" onclick="openLoginModal()">🔐 Iniciar Sesión Admin</button>
            <button id="btn-logout" class="btn btn-secondary btn-sm" style="display:none;" onclick="handleLogout()">Salir</button>
        </div>
    </header>

    <!-- ==================== NAVEGACIÓN ==================== -->
    <nav id="main-nav">
        <button class="nav-btn active" onclick="switchTab('cotizador')">📿 Cotizador Libre</button>
        <!-- Pestañas exclusivas para cuando inicia sesión con correo y contraseña -->
        <button class="nav-btn admin-only" style="display:none;" onclick="switchTab('facturacion')">🧾 Crear Venta / Facturar</button>
        <button class="nav-btn admin-only" style="display:none;" onclick="switchTab('inventario')">📦 Inventario & Banco</button>
        <button class="nav-btn admin-only" style="display:none;" onclick="switchTab('caja')">💰 Caja & Gastos</button>
        <button class="nav-btn admin-only" style="display:none;" onclick="switchTab('historial')">📑 Historial Facturas</button>
        <button class="nav-btn admin-only" style="display:none;" onclick="switchTab('usuarios')">👥 Usuarios & Respaldo</button>
        <button class="btn btn-secondary btn-sm" style="margin-left:auto;" onclick="reloadCurrentData()" title="Actualizar información">🔄 Actualizar</button>
    </nav>

    <div class="container">
        <!-- ==================== TAB 1: COTIZADOR (PÚBLICO SIN LOGIN) ==================== -->
        <div id="tab-cotizador" class="tab-content active">
            <div class="grid-2">
                <!-- Columna Izquierda: Selector de Balines -->
                <div class="card">
                    <div class="card-header">
                        <h2>🔘 Seleccionar Balines de la Manilla</h2>
                        <button class="btn btn-secondary btn-sm" onclick="resetQuote()">🗑️ Limpiar Todo</button>
                    </div>
                    <div id="quote-beads-list"></div>
                </div>

                <!-- Columna Derecha: Cálculo y Precio Editable -->
                <div class="card gold-border">
                    <div class="card-header">
                        <h2>💎 Valoración y Precio de Venta</h2>
                    </div>

                    <!-- Precio Calculado vs Precio Editable -->
                    <div class="stat-card" style="margin-bottom: 14px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span class="stat-label">Precio Calculado por Sistema</span>
                            <span id="quote-sub-display" style="font-size: 0.78rem; color: var(--gold-light); font-weight:600;">0 balines</span>
                        </div>
                        <div class="stat-value" id="quote-suggested-display" style="font-size:1.3rem; color:var(--text-muted);">$ 0</div>
                    </div>

                    <!-- CASILLA EDITABLE DE PRECIO FINAL / DESCUENTO -->
                    <div class="price-override-box">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <label style="font-size:0.8rem; font-weight:800; color:var(--gold-bright); text-transform:uppercase;">
                                ✏️ Precio Final en que la vas a Vender ($ COP):
                            </label>
                            <span id="quote-discount-badge" style="font-size:0.76rem; color:var(--info); font-weight:700;">Sin ajuste</span>
                        </div>
                        <input type="number" id="quote-custom-price" class="form-control price-input-large"
                               placeholder="0" step="1000" oninput="onCustomPriceInput(this.value)">
                        <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:10px;">
                            <button type="button" class="chip-btn" onclick="applyQuickRound('down5k')">🔽 Redondear a $5.000</button>
                            <button type="button" class="chip-btn" onclick="applyQuickRound('down10k')">🔽 Redondear a $10.000</button>
                            <button type="button" class="chip-btn" onclick="applyQuickRound('minus5k')">- $5.000 Desc.</button>
                            <button type="button" class="chip-btn" onclick="applyQuickRound('minus10k')">- $10.000 Desc.</button>
                            <button type="button" class="chip-btn" onclick="applyQuickRound('reset')">↺ Precio Exacto</button>
                        </div>
                    </div>

                    <div id="quote-rule-alert" class="rule-badge">
                        ℹ️ Selecciona balines para calcular la tarifa de confección.
                    </div>

                    <!-- Desglose Financiero -->
                    <div style="background: var(--bg-card-alt); border-radius: 12px; padding: 14px; margin-bottom: 16px; border: 1px solid var(--border-color);">
                        <div style="display:flex; justify-content:space-between; font-size:0.9rem; margin-bottom:6px;">
                            <span style="color:var(--text-muted);">Subtotal Balines:</span>
                            <span id="quote-beads-subtotal" style="font-weight:600;">$ 0</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; font-size:0.9rem; margin-bottom:6px;">
                            <span style="color:var(--gold-light);">Tarifa Confección / Manilla:</span>
                            <span id="quote-surcharge-val" style="color:var(--gold-primary); font-weight:700;">$ 0</span>
                        </div>
                        <div id="quote-discount-row" style="display:none; justify-content:space-between; font-size:0.88rem; margin-bottom:6px; color:var(--info);">
                            <span>Ajuste / Descuento aplicado:</span>
                            <span id="quote-discount-val" style="font-weight:700;">$ 0</span>
                        </div>

                        <div style="border-top:1px dashed var(--border-color); padding-top:10px; margin-top:8px;">
                            <div style="display:flex; justify-content:space-between; font-size:0.86rem; margin-bottom:5px; color:var(--text-muted);">
                                <span>Costo Base Materiales (Inversión):</span>
                                <span id="quote-cost-base" style="font-weight:600;">$ 0</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; font-size:1.08rem; font-weight:800; color:var(--success);">
                                <span>Ganancia Neta Real en esta Venta:</span>
                                <span id="quote-profit-val">$ 0</span>
                            </div>
                        </div>
                    </div>

                    <div style="display:flex; flex-direction:column; gap:10px;">
                        <button class="btn btn-secondary" onclick="copyQuoteForWhatsApp()">
                            📋 Copiar Cotización para WhatsApp
                        </button>
                        <button class="btn btn-gold" onclick="handleProceedToInvoice()">
                            🧾 Crear Venta y Facturar esta Manilla
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- ==================== TAB 2: FACTURACIÓN (REQUIERE LOGIN) ==================== -->
        <div id="tab-facturacion" class="tab-content admin-only">
            <div class="card gold-border">
                <div class="card-header">
                    <h2>🧾 Emitir Factura de Venta</h2>
                    <button class="btn btn-secondary btn-sm" onclick="switchTab('cotizador')">← Volver al Cotizador</button>
                </div>
                <div class="grid-3">
                    <div class="form-group">
                        <label>Nombre del Cliente *</label>
                        <input type="text" id="inv-customer-name" class="form-control" placeholder="Ej: Cliente Mostrador / Nombre" required>
                    </div>
                    <div class="form-group">
                        <label>Teléfono / WhatsApp</label>
                        <input type="text" id="inv-customer-phone" class="form-control" placeholder="Ej: 310 123 4567">
                    </div>
                    <div class="form-group">
                        <label>Documento / C.C. (Opcional)</label>
                        <input type="text" id="inv-customer-id" class="form-control" placeholder="C.C. o NIT">
                    </div>
                </div>

                <div style="background: var(--bg-card-alt); border-radius: 12px; padding: 16px; margin: 14px 0; border: 1px solid var(--border-color);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                        <h4 style="color: var(--gold-light);">DETALLE DE LA MANILLA A FACTURAR</h4>
                        <button class="btn btn-secondary btn-sm" onclick="switchTab('cotizador')">+ Cambiar Balines en Cotizador</button>
                    </div>
                    <div id="invoice-items-summary" style="color: var(--text-muted); font-size:0.9rem;">
                        No hay manillas cargadas. Selecciona los balines en el Cotizador y pulsa "Crear Venta".
                    </div>
                    <div id="invoice-items-table-container" style="display:none;" class="table-responsive">
                        <table>
                            <thead>
                                <tr><th>Descripción</th><th>Balines</th><th>Precio Calculado</th><th>Precio Final Venta</th><th>Ganancia Neta</th><th></th></tr>
                            </thead>
                            <tbody id="invoice-items-tbody"></tbody>
                        </table>
                    </div>
                </div>

                <div class="grid-2">
                    <div class="form-group">
                        <label>Observaciones (Color de hilo, medida, método de pago, etc.)</label>
                        <textarea id="inv-notes" class="form-control" rows="4" placeholder="Ej: Hilo negro tejido macramé, 18cm, pago por Nequi/Bancolombia..."></textarea>
                    </div>
                    <div style="background: var(--bg-card-alt); padding: 16px; border-radius: 12px; border: 1px solid var(--border-gold);">
                        <div style="display:flex; justify-content:space-between; margin-bottom:6px; font-size:0.9rem;">
                            <span style="color:var(--text-muted);">Subtotal Balines + Confección:</span>
                            <span id="inv-summary-suggested">$ 0</span>
                        </div>
                        <div style="display:flex; justify-content:space-between; margin-bottom:10px; font-size:0.88rem; color:var(--info);">
                            <span>Descuento / Redondeo:</span>
                            <span id="inv-summary-discount">$ 0</span>
                        </div>
                        <div class="form-group" style="margin-bottom:8px;">
                            <label style="color:var(--gold-bright);">PRECIO FINAL A COBRAR ($ COP) - Editable:</label>
                            <input type="number" id="inv-final-price-input" class="form-control price-input-large" step="1000" oninput="updateInvoiceFinalPrice(this.value)">
                        </div>
                        <div style="display:flex; justify-content:space-between; font-size:0.95rem; font-weight:700; color:var(--success); border-top:1px solid var(--border-color); padding-top:8px;">
                            <span>Ganancia Real para el Negocio:</span>
                            <span id="inv-summary-profit">$ 0</span>
                        </div>
                    </div>
                </div>

                <div style="margin-top: 18px; display: flex; gap: 12px; flex-wrap:wrap;">
                    <button class="btn btn-gold" style="flex: 2; min-width:220px; padding:14px; font-size:1rem;" onclick="submitInvoice()">
                        💾 Confirmar Venta, Descontar Inventario y Sumar a Cuenta
                    </button>
                    <button class="btn btn-secondary" style="flex: 1;" onclick="clearInvoiceForm()">Limpiar</button>
                </div>
            </div>
        </div>

        <!-- ==================== TAB 3: INVENTARIO Y RESUMEN BANCARIO ==================== -->
        <div id="tab-inventario" class="tab-content admin-only">
            <!-- TARJETAS DE RESUMEN DE CUENTA BANCARIA, GANANCIAS E INVENTARIO -->
            <div class="grid-4" style="margin-bottom: 18px;">
                <div class="stat-card gold-border">
                    <div class="stat-label">🏦 Total en Cuenta Bancaria / Caja</div>
                    <div class="stat-value" id="inv-stat-bank" style="color:var(--gold-bright);">$ 0</div>
                    <div class="stat-sub" id="inv-stat-bank-sub">Ingresos menos gastos totales</div>
                </div>
                <div class="stat-card success-border">
                    <div class="stat-label">📈 Ganancia Neta Real (Ventas)</div>
                    <div class="stat-value" id="inv-stat-real-profit" style="color:var(--success);">$ 0</div>
                    <div class="stat-sub" id="inv-stat-sales-count">De 0 facturas emitidas</div>
                </div>
                <div class="stat-card info-border">
                    <div class="stat-label">📦 Total Balines Tenemos en Stock</div>
                    <div class="stat-value" id="inv-stat-total-units" style="color:var(--info);">0 unids</div>
                    <div class="stat-sub">Costo invertido: <strong id="inv-stat-cost-val" style="color:#fff;">$ 0</strong></div>
                </div>
                <div class="stat-card success-border">
                    <div class="stat-label">💎 Ganancia Potencial Stock Actual</div>
                    <div class="stat-value" id="inv-stat-pot-profit" style="color:var(--gold-light);">$ 0</div>
                    <div class="stat-sub">Venta estimada stock: <strong id="inv-stat-sale-val" style="color:#fff;">$ 0</strong></div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <h2>📦 Control de Inventario de Balines y Precios</h2>
                    <div style="display:flex; gap:10px; flex-wrap:wrap;">
                        <button class="btn btn-gold btn-sm" onclick="openBulkStockModal()">✏️ Ajustar Conteo Físico (Todos)</button>
                        <button class="btn btn-secondary btn-sm" onclick="openRestockModal()">+ Registrar Compra de Balines</button>
                    </div>
                </div>
                <p style="color:var(--text-muted); font-size:0.85rem; margin-bottom:15px;">
                    💡 Cambia las unidades de cualquier balín y pulsa <strong>💾</strong> para guardar inmediatamente. Todos los cambios quedan respaldados.
                </p>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>Tamaño</th>
                                <th style="min-width:140px;">Unidades (Stock)</th>
                                <th>Costo Unidad</th>
                                <th>Dinero en Stock (Costo)</th>
                                <th>% Gan. Unit</th>
                                <th>Precio Venta Unit.</th>
                                <th>Ganancia x Unidad</th>
                                <th>Acciones</th>
                            </tr>
                        </thead>
                        <tbody id="inventory-tbody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ==================== TAB 4: FLUJO DE CAJA ==================== -->
        <div id="tab-caja" class="tab-content admin-only">
            <div class="grid-4" style="margin-bottom: 20px;">
                <div class="stat-card success-border">
                    <div class="stat-label">Total Ingresos (Ventas/Entradas)</div>
                    <div class="stat-value" id="cash-total-in" style="color:var(--success);">$ 0</div>
                </div>
                <div class="stat-card danger-border">
                    <div class="stat-label">Total Salidas (Compras/Gastos)</div>
                    <div class="stat-value" id="cash-total-out" style="color:var(--danger);">$ 0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">🏦 Saldo Total Cuenta Bancaria</div>
                    <div class="stat-value" id="cash-balance" style="color:var(--gold-bright);">$ 0</div>
                </div>
                <div class="stat-card success-border">
                    <div class="stat-label">✨ Ganancia Neta de Manillas</div>
                    <div class="stat-value" id="cash-real-profit" style="color:var(--success);">$ 0</div>
                    <div class="stat-sub">Capital costo recuperado: <span id="cash-recovered-cost">$ 0</span></div>
                </div>
            </div>

            <div class="grid-2">
                <div class="card">
                    <div class="card-header"><h2>💸 Registrar Movimiento en Cuenta / Caja</h2></div>
                    <form onsubmit="submitCashFlow(event)">
                        <div class="grid-2">
                            <div class="form-group">
                                <label>Tipo de Movimiento</label>
                                <select id="cf-type" class="form-control" required>
                                    <option value="SALIDA">SALIDA (Gasto / Compra)</option>
                                    <option value="INGRESO">INGRESO (Entrada / Abono)</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>Categoría</label>
                                <select id="cf-category" class="form-control" required>
                                    <option value="Compra de Balines">Compra de Balines</option>
                                    <option value="Hilos y Materiales">Hilos y Materiales</option>
                                    <option value="Broches y Accesorios">Broches y Accesorios</option>
                                    <option value="Empaques y Bolsas">Empaques y Cajas de Lujo</option>
                                    <option value="Envíos y Domicilios">Envíos / Domicilios</option>
                                    <option value="Venta Directa">Venta Directa</option>
                                    <option value="Aporte de Capital">Aporte Inicial en Cuenta</option>
                                    <option value="Otro">Otro Movimiento</option>
                                </select>
                            </div>
                        </div>
                        <div class="form-group">
                            <label>Descripción</label>
                            <input type="text" id="cf-desc" class="form-control" placeholder="Ej: Compra rollos de hilo terlenca / Venta..." required>
                        </div>
                        <div class="grid-2">
                            <div class="form-group">
                                <label>Monto ($ COP)</label>
                                <input type="number" id="cf-amount" class="form-control" min="1" step="100" required>
                            </div>
                            <div class="form-group">
                                <label>Fecha</label>
                                <input type="date" id="cf-date" class="form-control" required>
                            </div>
                        </div>
                        <button type="submit" class="btn btn-gold" style="width:100%;">💾 Guardar Movimiento</button>
                    </form>
                </div>

                <div class="card">
                    <div class="card-header"><h2>📋 Historial de Movimientos Bancarios / Caja</h2></div>
                    <div class="table-responsive" style="max-height: 430px; overflow-y: auto;">
                        <table>
                            <thead><tr><th>Fecha</th><th>Tipo</th><th>Detalle</th><th>Monto</th><th></th></tr></thead>
                            <tbody id="cash-flow-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <!-- ==================== TAB 5: HISTORIAL FACTURAS ==================== -->
        <div id="tab-historial" class="tab-content admin-only">
            <div class="card">
                <div class="card-header"><h2>📑 Facturas Emitidas y Ganancias por Venta</h2></div>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>Factura</th>
                                <th>Fecha</th>
                                <th>Cliente</th>
                                <th>Precio Calculado</th>
                                <th>Vendido En (Real)</th>
                                <th>Costo Balines</th>
                                <th>Ganancia Neta</th>
                                <th>Acciones</th>
                            </tr>
                        </thead>
                        <tbody id="invoices-tbody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ==================== TAB 6: USUARIOS Y RESPALDO ==================== -->
        <div id="tab-usuarios" class="tab-content admin-only">
            <div class="card gold-border" style="margin-bottom:20px;">
                <div class="card-header">
                    <h2>☁️ Respaldo y Sincronización Permanente de Datos</h2>
                </div>
                <p style="font-size:0.88rem; color:var(--text-muted); margin-bottom:14px;">
                    El sistema guarda automáticamente una copia espejo en tu navegador y la restaura si el servidor gratuito de Render se reinicia. Además, puedes descargar o cargar un archivo de respaldo cuando quieras pasar información entre dispositivos:
                </p>
                <div style="display:flex; gap:12px; flex-wrap:wrap;">
                    <button class="btn btn-gold" onclick="downloadBackupFile()">📥 Descargar Copia de Seguridad (.JSON)</button>
                    <label class="btn btn-secondary" style="cursor:pointer;">
                        📤 Restaurar Copia desde Archivo
                        <input type="file" accept=".json" style="display:none;" onchange="uploadBackupFile(event)">
                    </label>
                </div>
            </div>

            <div class="grid-2">
                <div class="card">
                    <div class="card-header"><h2>➕ Crear Nuevo Usuario Administrador</h2></div>
                    <form onsubmit="handleCreateUser(event)">
                        <div class="form-group">
                            <label>Nombre Completo</label>
                            <input type="text" id="new-user-name" class="form-control" placeholder="Ej: Harold Itanare" required>
                        </div>
                        <div class="form-group">
                            <label>Correo Electrónico</label>
                            <input type="email" id="new-user-email" class="form-control" placeholder="correo@joyeria.com" required>
                        </div>
                        <div class="form-group">
                            <label>Contraseña</label>
                            <input type="password" id="new-user-password" class="form-control" placeholder="Mínimo 6 caracteres" required>
                        </div>
                        <input type="hidden" id="new-user-role" value="ADMIN">
                        <button type="submit" class="btn btn-gold" style="width:100%;">Guardar Usuario</button>
                    </form>
                </div>

                <div class="card">
                    <div class="card-header"><h2>👥 Usuarios con Acceso</h2></div>
                    <div class="table-responsive">
                        <table>
                            <thead><tr><th>Nombre</th><th>Correo</th><th>Rol</th><th>Acción</th></tr></thead>
                            <tbody id="users-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- ==================== MODALES ==================== -->

    <!-- MODAL DE INICIO DE SESIÓN (PARA FACTURAR / ADMINISTRAR) -->
    <div id="login-modal" class="modal-overlay">
        <div class="modal-content modal-dark" style="max-width:420px; text-align:center;">
            <div style="font-size: 2.3rem; margin-bottom: 6px;">🔐</div>
            <h2 style="color:var(--gold-bright); margin-bottom:4px;">Acceso Administrativo</h2>
            <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 18px;">
                Ingresa tu correo y contraseña para registrar ventas, editar inventario y ver las ganancias de la cuenta.
            </p>

            <div id="login-error-box" style="display:none; background: var(--danger-bg); border: 1px solid var(--danger); color: #FCA5A5; padding: 10px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 14px;"></div>

            <form onsubmit="handleLogin(event)" style="text-align: left;">
                <div class="form-group">
                    <label>Correo Electrónico</label>
                    <input type="email" id="login-email" class="form-control" placeholder="admin@joyeria.com" required autocomplete="username">
                </div>
                <div class="form-group">
                    <label>Contraseña</label>
                    <input type="password" id="login-password" class="form-control" placeholder="••••••••" required autocomplete="current-password">
                </div>
                <div style="display:flex; gap:10px; margin-top:16px;">
                    <button type="submit" class="btn btn-gold" style="flex:2;">🔑 Entrar</button>
                    <button type="button" class="btn btn-secondary" style="flex:1;" onclick="closeLoginModal()">Cancelar</button>
                </div>
            </form>

            <div style="margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--border-color); font-size: 0.78rem; color: var(--text-muted); text-align: left;">
                👑 <strong>Acceso por defecto:</strong> <code>admin@joyeria.com</code> / <code>admin123</code>
            </div>
        </div>
    </div>

    <!-- MODAL IMPRIMIR FACTURA -->
    <div id="invoice-modal" class="modal-overlay">
        <div class="modal-content">
            <div class="inv-header">
                <h2>✨ JOYERÍA ORO & BALINES 18K ✨</h2>
                <p style="font-weight:600; color:#555;">VENTA DE MANILLAS CON BALINES DIAMANTADOS EN ORO</p>
                <p style="font-size:0.8rem; color:#777;">Calidad Garantizada • Oro 18K</p>
                <div style="margin-top:6px; font-weight:800; color:#b45309; font-size:1.05rem;" id="modal-inv-number">FACTURA N°</div>
            </div>
            <div class="grid-2" style="font-size:0.88rem; margin-bottom:10px;">
                <div><p><strong>Cliente:</strong> <span id="modal-inv-customer"></span></p><p><strong>Tel:</strong> <span id="modal-inv-phone"></span></p></div>
                <div style="text-align:right;"><p><strong>Fecha:</strong> <span id="modal-inv-date"></span></p><p><strong>Doc:</strong> <span id="modal-inv-doc"></span></p></div>
            </div>
            <table class="inv-table">
                <thead><tr><th>Descripción</th><th>Balines</th><th style="text-align:right;">Subtotal</th></tr></thead>
                <tbody id="modal-inv-tbody"></tbody>
            </table>
            <div class="inv-totals">
                <div class="inv-totals-row"><span>Subtotal Balines:</span><span id="modal-inv-subtotal">$ 0</span></div>
                <div class="inv-totals-row"><span id="modal-inv-rule-label">Confección:</span><span id="modal-inv-surcharge">$ 0</span></div>
                <div class="inv-totals-row" id="modal-inv-discount-row" style="display:none; color:#0284c7;">
                    <span>Descuento / Ajuste Especial:</span><span id="modal-inv-discount">$ 0</span>
                </div>
                <div class="inv-totals-row grand-total"><span>TOTAL PAGADO:</span><span id="modal-inv-total">$ 0</span></div>
            </div>
            <div id="modal-inv-notes-box" style="background:#f8fafc; padding:10px; border-radius:6px; font-size:0.84rem; margin-top:12px; border:1px solid #e2e8f0;">
                <strong>Observaciones:</strong> <span id="modal-inv-notes"></span>
            </div>
            <div style="display:flex; gap:10px; margin-top:18px;" class="no-print">
                <button class="btn btn-gold" style="flex:1;" onclick="window.print()">🖨️ Imprimir / Guardar PDF</button>
                <button class="btn btn-secondary" style="flex:1;" onclick="closeModal()">Cerrar</button>
            </div>
        </div>
    </div>

    <!-- MODAL EDITAR GANANCIAS Y COSTOS -->
    <div id="edit-bead-modal" class="modal-overlay">
        <div class="modal-content modal-dark" style="max-width:480px;">
            <div class="card-header">
                <h3>✏️ Modificar Costo y % de Ganancia</h3>
            </div>
            <form onsubmit="submitBeadMargins(event)">
                <input type="hidden" id="edit-bead-id">
                <div style="background:var(--bg-card-alt); padding:10px 14px; border-radius:10px; margin-bottom:14px;">
                    <span style="font-size:0.8rem; color:var(--text-muted);">Balín seleccionado:</span>
                    <h3 id="edit-bead-title" style="color:var(--gold-light);">Balín 3 mm</h3>
                </div>
                <div class="form-group">
                    <label>Costo Inicial / Proveedor ($ COP)</label>
                    <input type="number" id="edit-bead-cost" class="form-control" min="0" step="100" oninput="recalcBeadPreview()" required>
                </div>
                <div class="grid-2">
                    <div class="form-group">
                        <label>% Ganancia Por Unidad</label>
                        <input type="number" id="edit-bead-gain-unit" class="form-control" min="0" max="500" step="0.5" oninput="recalcBeadPreview()" required>
                    </div>
                    <div class="form-group">
                        <label>Precio Venta Unitario</label>
                        <input type="text" id="edit-preview-unit-price" class="form-control" style="color:var(--gold-bright); font-weight:700;" readonly>
                    </div>
                </div>
                <div class="grid-2">
                    <div class="form-group">
                        <label>% Ganancia x20 (Por Mayor)</label>
                        <input type="number" id="edit-bead-gain-x20" class="form-control" min="0" max="500" step="0.5" oninput="recalcBeadPreview()" required>
                    </div>
                    <div class="form-group">
                        <label>Precio Unitario x20</label>
                        <input type="text" id="edit-preview-x20-unit" class="form-control" readonly>
                    </div>
                </div>
                <div style="background:var(--bg-card-alt); padding:10px 14px; border-radius:10px; margin: 8px 0 18px 0; display:flex; justify-content:space-between;">
                    <span style="font-size:0.85rem; color:var(--text-muted);">Total Paquete x20:</span>
                    <strong id="edit-preview-x20-total" style="color:var(--gold-bright); font-size:1.1rem;">$ 0</strong>
                </div>
                <div style="display:flex; gap:10px;">
                    <button type="submit" class="btn btn-gold" style="flex:1;">💾 Guardar Cambios</button>
                    <button type="button" class="btn btn-secondary" style="flex:1;" onclick="closeEditBeadModal()">Cancelar</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL AJUSTAR TODO EL INVENTARIO -->
    <div id="bulk-stock-modal" class="modal-overlay">
        <div class="modal-content modal-dark" style="max-width:480px;">
            <div class="card-header">
                <h3>✏️ Ajustar Conteo Físico Real</h3>
            </div>
            <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:15px;">
                Escribe la cantidad exacta de balines que tienes físicamente en este momento:
            </p>
            <form onsubmit="submitBulkStock(event)">
                <div id="bulk-stock-inputs" style="display:grid; grid-template-columns: 1fr 1fr; gap:12px; margin-bottom:20px;"></div>
                <div style="display:flex; gap:10px;">
                    <button type="submit" class="btn btn-gold" style="flex:1;">💾 Guardar Todo el Inventario</button>
                    <button type="button" class="btn btn-secondary" style="flex:1;" onclick="closeBulkStockModal()">Cancelar</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL ENTRADA STOCK -->
    <div id="restock-modal" class="modal-overlay">
        <div class="modal-content modal-dark" style="max-width:440px;">
            <div class="card-header"><h3>📦 Registrar Compra de Balines</h3></div>
            <form onsubmit="submitRestock(event)">
                <div class="form-group">
                    <label>Tamaño del Balín</label>
                    <select id="restock-size" class="form-control" required>
                        <option value="3 mm">3 mm</option>
                        <option value="4 mm">4 mm</option>
                        <option value="5 mm">5 mm</option>
                        <option value="6 mm">6 mm</option>
                        <option value="7 mm">7 mm</option>
                        <option value="8 mm">8 mm</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Cantidad Comprada (Unidades)</label>
                    <input type="number" id="restock-qty" class="form-control" min="1" placeholder="Ej: 20" required>
                </div>
                <div class="form-group" style="display:flex; align-items:center; gap:8px;">
                    <input type="checkbox" id="restock-record-expense" checked>
                    <label for="restock-record-expense" style="margin:0; text-transform:none; color:var(--gold-light);">
                        Descontar el valor de esta compra en Caja / Cuenta Bancaria
                    </label>
                </div>
                <div style="display:flex; gap:10px; margin-top:15px;">
                    <button type="submit" class="btn btn-gold" style="flex:1;">Guardar Compra</button>
                    <button type="button" class="btn btn-secondary" style="flex:1;" onclick="closeRestockModal()">Cancelar</button>
                </div>
            </form>
        </div>
    </div>

    <!-- ==================== JAVASCRIPT ==================== -->
    <script>
        const MIRROR_STORAGE_KEY = 'joyeria_db_mirror_v2';
        let currentUser = null;
        let inventoryData = [];
        let quoteSelection = {};
        let currentQuoteResult = null;
        let customOverridePrice = null; // null = usar sugerido, número = precio editado por el usuario
        let pendingInvoiceItems = [];
        let pendingTransferAfterLogin = false;

        function formatCOP(num) {
            return '$ ' + Math.round(num || 0).toLocaleString('es-CO');
        }

        function getAuthHeaders() {
            const token = localStorage.getItem('auth_token') || '';
            return {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + token
            };
        }

        // ==================== AUTO-SINCRONIZACIÓN CONTRA REINICIOS DE RENDER ====================
        async function syncWithLocalMirror() {
            try {
                const localRaw = localStorage.getItem(MIRROR_STORAGE_KEY);
                const localBackup = localRaw ? JSON.parse(localRaw) : null;

                const res = await fetch('/api/backup');
                if (!res.ok) return;
                const serverBackup = await res.json();

                const serverEmpty = (!serverBackup.updated_at) &&
                    serverBackup.invoices.length === 0 &&
                    serverBackup.cash_flow.length === 0 &&
                    serverBackup.beads_inventory.every(b => (b.stock || 0) === 0);

                // Si Render se durmió y amaneció en cero, pero el dispositivo tiene el respaldo, lo restaura automáticamente
                if (serverEmpty && localBackup && localBackup.updated_at) {
                    await fetch('/api/restore-mirror', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(localBackup)
                    });
                    console.log("Respaldo restaurado automáticamente al servidor.");
                } else if (serverBackup.updated_at) {
                    localStorage.setItem(MIRROR_STORAGE_KEY, JSON.stringify(serverBackup));
                }
            } catch (e) {
                console.warn("Mirror sync warning:", e);
            }
        }

        async function saveServerToLocalMirror() {
            try {
                const res = await fetch('/api/backup');
                if (res.ok) {
                    const data = await res.json();
                    localStorage.setItem(MIRROR_STORAGE_KEY, JSON.stringify(data));
                }
            } catch (e) {}
        }

        // ==================== AUTENTICACIÓN NO BLOQUEANTE ====================
        async function initApp() {
            await syncWithLocalMirror();
            await loadInventory();

            const token = localStorage.getItem('auth_token');
            if (token) {
                try {
                    const res = await fetch('/api/me', { headers: getAuthHeaders() });
                    if (res.ok) {
                        currentUser = await res.json();
                        updateAuthUI();
                        loadSummaryStats();
                        return;
                    } else {
                        localStorage.removeItem('auth_token');
                    }
                } catch (e) {}
            }
            currentUser = null;
            updateAuthUI();
        }

        function updateAuthUI() {
            const roleBadge = document.getElementById('user-display-badge');
            const emailSpan = document.getElementById('user-display-email');
            const btnLogin = document.getElementById('btn-open-login');
            const btnLogout = document.getElementById('btn-logout');
            const adminElements = document.querySelectorAll('.admin-only');

            if (currentUser) {
                roleBadge.innerText = '👑 ADMIN ACTIVO';
                roleBadge.className = 'badge-role badge-admin';
                emailSpan.innerText = currentUser.email;
                emailSpan.style.display = 'inline';
                btnLogin.style.display = 'none';
                btnLogout.style.display = 'inline-flex';
                adminElements.forEach(el => el.style.display = '');
            } else {
                roleBadge.innerText = '📿 MODO COTIZADOR LIBRE';
                roleBadge.className = 'badge-role badge-guest';
                emailSpan.style.display = 'none';
                btnLogin.style.display = 'inline-flex';
                btnLogout.style.display = 'none';
                adminElements.forEach(el => el.style.display = 'none');
                switchTab('cotizador');
            }
        }

        function openLoginModal() {
            document.getElementById('login-error-box').style.display = 'none';
            document.getElementById('login-modal').classList.add('active');
            setTimeout(() => document.getElementById('login-email').focus(), 100);
        }

        function closeLoginModal() {
            document.getElementById('login-modal').classList.remove('active');
            pendingTransferAfterLogin = false;
        }

        async function handleLogin(e) {
            e.preventDefault();
            const email = document.getElementById('login-email').value.trim();
            const password = document.getElementById('login-password').value;
            const errBox = document.getElementById('login-error-box');
            errBox.style.display = 'none';

            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password })
                });
                const data = await res.json();
                if (data.success) {
                    localStorage.setItem('auth_token', data.token);
                    currentUser = data.user;
                    document.getElementById('login-modal').classList.remove('active');
                    updateAuthUI();
                    await loadInventory();
                    await loadSummaryStats();

                    if (pendingTransferAfterLogin) {
                        pendingTransferAfterLogin = false;
                        executeTransferToInvoice();
                    }
                } else {
                    errBox.innerText = data.error || 'Correo o contraseña incorrectos.';
                    errBox.style.display = 'block';
                }
            } catch (err) {
                errBox.innerText = 'Error al conectar con el servidor.';
                errBox.style.display = 'block';
            }
        }

        async function handleLogout() {
            try {
                await fetch('/api/logout', { method: 'POST', headers: getAuthHeaders() });
            } catch (err) {}
            localStorage.removeItem('auth_token');
            currentUser = null;
            updateAuthUI();
        }

        function switchTab(tabId) {
            if (!currentUser && tabId !== 'cotizador') {
                openLoginModal();
                return;
            }

            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));

            const target = document.getElementById('tab-' + tabId);
            if (target) target.classList.add('active');

            const btns = Array.from(document.querySelectorAll('.nav-btn'));
            const matched = btns.find(b => b.getAttribute('onclick') && b.getAttribute('onclick').includes(tabId));
            if (matched) matched.classList.add('active');

            if (tabId === 'inventario') { loadInventory(); loadSummaryStats(); }
            if (tabId === 'caja') { loadCashFlow(); loadSummaryStats(); }
            if (tabId === 'historial') loadInvoices();
            if (tabId === 'usuarios') loadUsers();
        }

        async function reloadCurrentData() {
            await syncWithLocalMirror();
            await loadInventory();
            if (currentUser) {
                await loadSummaryStats();
                await loadCashFlow();
                await loadInvoices();
                await loadUsers();
            }
        }

        // ==================== RESUMEN FINANCIERO Y BANCARIO ====================
        async function loadSummaryStats() {
            if (!currentUser) return;
            try {
                const res = await fetch('/api/summary', { headers: getAuthHeaders() });
                if (!res.ok) return;
                const s = await res.json();

                document.getElementById('inv-stat-bank').innerText = formatCOP(s.bank_balance);
                document.getElementById('inv-stat-bank-sub').innerText = `Ingresos: ${formatCOP(s.total_income)} | Gastos: ${formatCOP(s.total_expense)}`;
                document.getElementById('inv-stat-real-profit').innerText = formatCOP(s.total_realized_profit);
                document.getElementById('inv-stat-sales-count').innerText = `En ${s.invoices_count} ventas (${formatCOP(s.total_invoiced_sales)} facturado)`;
                document.getElementById('inv-stat-total-units').innerText = `${s.total_stock_units} balines`;
                document.getElementById('inv-stat-cost-val').innerText = formatCOP(s.inventory_cost_value);
                document.getElementById('inv-stat-pot-profit').innerText = formatCOP(s.inventory_potential_profit);
                document.getElementById('inv-stat-sale-val').innerText = formatCOP(s.inventory_sale_value);

                document.getElementById('cash-real-profit').innerText = formatCOP(s.total_realized_profit);
                document.getElementById('cash-recovered-cost').innerText = formatCOP(s.total_recovered_cost);
            } catch (e) {}
        }

        // ==================== INVENTARIO & COTIZADOR ====================
        async function loadInventory() {
            try {
                const res = await fetch('/api/inventory', { headers: getAuthHeaders() });
                if (!res.ok) return;
                inventoryData = await res.json();
                renderInventoryTable();
                renderQuoteBeadsList();
            } catch (err) {
                console.error("Error cargando inventario:", err);
            }
        }

        function renderQuoteBeadsList() {
            const container = document.getElementById('quote-beads-list');
            container.innerHTML = '';
            inventoryData.forEach(item => {
                if (!(item.size in quoteSelection)) quoteSelection[item.size] = 0;
                const qty = quoteSelection[item.size] || 0;
                const row = document.createElement('div');
                row.className = 'bead-row' + (qty > 0 ? ' selected-row' : '');
                row.id = 'row-' + item.size.replace(' ', '');
                row.innerHTML = `
                    <div>
                        <div style="font-weight:700; font-size:0.98rem; color:#fff;">Balín Diamantado ${item.size}</div>
                        <div style="font-size:0.82rem; color:var(--text-muted); margin-top:2px;">
                            Unitario: <strong style="color:var(--gold-bright);">${formatCOP(item.price_unit)}</strong>
                            | Stock: <span style="color:${item.stock > 0 ? 'var(--success)' : 'var(--danger)'}; font-weight:700;">${item.stock} disponibles</span>
                        </div>
                    </div>
                    <div class="bead-counter">
                        <button type="button" onclick="changeQuoteQty('${item.size}', -1)">−</button>
                        <input type="number" id="input-${item.size.replace(' ', '')}" value="${qty}" min="0" oninput="setQuoteQty('${item.size}', this.value)">
                        <button type="button" onclick="changeQuoteQty('${item.size}', 1)">+</button>
                    </div>
                `;
                container.appendChild(row);
            });
            calculateQuoteLive(false);
        }

        function changeQuoteQty(size, delta) {
            quoteSelection[size] = Math.max(0, (quoteSelection[size] || 0) + delta);
            const key = size.replace(' ', '');
            document.getElementById('input-' + key).value = quoteSelection[size];
            const row = document.getElementById('row-' + key);
            if (row) {
                if (quoteSelection[size] > 0) row.classList.add('selected-row');
                else row.classList.remove('selected-row');
            }
            customOverridePrice = null; // Al cambiar balines se recalcula el sugerido
            calculateQuoteLive(true);
        }

        function setQuoteQty(size, val) {
            quoteSelection[size] = Math.max(0, parseInt(val) || 0);
            const key = size.replace(' ', '');
            const row = document.getElementById('row-' + key);
            if (row) {
                if (quoteSelection[size] > 0) row.classList.add('selected-row');
                else row.classList.remove('selected-row');
            }
            customOverridePrice = null;
            calculateQuoteLive(true);
        }

        function resetQuote() {
            for (let s in quoteSelection) quoteSelection[s] = 0;
            customOverridePrice = null;
            renderQuoteBeadsList();
        }

        function onCustomPriceInput(val) {
            const parsed = parseFloat(val);
            if (!isNaN(parsed) && parsed >= 0) {
                customOverridePrice = parsed;
            } else {
                customOverridePrice = null;
            }
            calculateQuoteLive(false);
        }

        function applyQuickRound(mode) {
            if (!currentQuoteResult || currentQuoteResult.suggestedPrice <= 0) return;
            const sug = currentQuoteResult.suggestedPrice;
            const current = customOverridePrice !== null ? customOverridePrice : sug;

            if (mode === 'down5k') {
                customOverridePrice = Math.floor(sug / 5000) * 5000;
            } else if (mode === 'down10k') {
                customOverridePrice = Math.floor(sug / 10000) * 10000;
            } else if (mode === 'minus5k') {
                customOverridePrice = Math.max(0, current - 5000);
            } else if (mode === 'minus10k') {
                customOverridePrice = Math.max(0, current - 10000);
            } else if (mode === 'reset') {
                customOverridePrice = null;
            }
            calculateQuoteLive(true);
        }

        function calculateQuoteLive(updateInputBox = true) {
            let totalBeadsCount = 0;
            let costBase = 0;
            let subtotalProfit = 0;
            const breakdown = [];

            inventoryData.forEach(item => {
                const qty = quoteSelection[item.size] || 0;
                if (qty > 0) {
                    totalBeadsCount += qty;
                    costBase += qty * (item.cost || 0);
                    const price = qty * item.price_unit;
                    subtotalProfit += price;
                    breakdown.push({ size: item.size, qty: qty, price: price });
                }
            });

            let surcharge = 0;
            let ruleLabel = "";
            let isHighValue = false;

            if (subtotalProfit > 0) {
                if (subtotalProfit < 500000) {
                    surcharge = 50000;
                    ruleLabel = "Menor a $500.000: Se suman $50.000 fijos de confección de manilla.";
                } else {
                    surcharge = Math.round(subtotalProfit * 0.15);
                    ruleLabel = "Mayor o igual a $500.000: Se suma 15% de confección de manilla.";
                    isHighValue = true;
                }
            } else {
                ruleLabel = "Selecciona balines para calcular la tarifa.";
            }

            const suggestedPrice = Math.round(subtotalProfit + surcharge);
            const finalPrice = (customOverridePrice !== null && subtotalProfit > 0) ? Math.round(customOverridePrice) : suggestedPrice;
            const discountAmount = suggestedPrice - finalPrice;
            const netProfit = finalPrice - costBase;

            currentQuoteResult = {
                totalBeadsCount,
                breakdown,
                costBase,
                subtotalProfit,
                surcharge,
                ruleLabel,
                isHighValue,
                suggestedPrice,
                finalPrice,
                discountAmount,
                netProfit
            };

            document.getElementById('quote-suggested-display').innerText = formatCOP(suggestedPrice);
            document.getElementById('quote-sub-display').innerText = `${totalBeadsCount} balines seleccionados`;

            const customInput = document.getElementById('quote-custom-price');
            if (updateInputBox || document.activeElement !== customInput) {
                customInput.value = subtotalProfit > 0 ? finalPrice : '';
            }

            const discBadge = document.getElementById('quote-discount-badge');
            const discRow = document.getElementById('quote-discount-row');
            const discVal = document.getElementById('quote-discount-val');

            if (discountAmount > 0 && subtotalProfit > 0) {
                discBadge.innerText = `Descuento: -${formatCOP(discountAmount)}`;
                discBadge.style.color = 'var(--info)';
                discRow.style.display = 'flex';
                discVal.innerText = `- ${formatCOP(discountAmount)}`;
            } else if (discountAmount < 0 && subtotalProfit > 0) {
                discBadge.innerText = `Adicional: +${formatCOP(Math.abs(discountAmount))}`;
                discBadge.style.color = 'var(--gold-bright)';
                discRow.style.display = 'flex';
                discVal.innerText = `+ ${formatCOP(Math.abs(discountAmount))}`;
            } else {
                discBadge.innerText = 'Precio sugerido exacto';
                discBadge.style.color = 'var(--text-muted)';
                discRow.style.display = 'none';
            }

            const alertBox = document.getElementById('quote-rule-alert');
            alertBox.innerText = (isHighValue ? "⭐ " : "ℹ️ ") + ruleLabel;
            if (isHighValue) alertBox.classList.add('high-value'); else alertBox.classList.remove('high-value');

            document.getElementById('quote-cost-base').innerText = formatCOP(costBase);
            document.getElementById('quote-beads-subtotal').innerText = formatCOP(subtotalProfit);
            document.getElementById('quote-surcharge-val').innerText = formatCOP(surcharge);

            const profitEl = document.getElementById('quote-profit-val');
            profitEl.innerText = formatCOP(netProfit);
            profitEl.style.color = netProfit >= 0 ? 'var(--success)' : 'var(--danger)';
        }

        function copyQuoteForWhatsApp() {
            if (!currentQuoteResult || currentQuoteResult.totalBeadsCount === 0) {
                alert("Selecciona al menos 1 balín para generar la cotización.");
                return;
            }

            let text = `✨ *COTIZACIÓN DE MANILLA EN ORO 18K* ✨\n`;
            text += `Joyería Fina & Balines Diamantados\n\n`;
            text += `💎 *Detalle de balines seleccionados:*\n`;
            currentQuoteResult.breakdown.forEach(b => {
                text += `• ${b.qty} balines de ${b.size}: ${formatCOP(b.price)}\n`;
            });
            text += `\n📌 *Subtotal balines:* ${formatCOP(currentQuoteResult.subtotalProfit)}\n`;
            text += `🧵 *Confección y tejido:* ${formatCOP(currentQuoteResult.surcharge)}\n`;
            if (currentQuoteResult.discountAmount > 0) {
                text += `🎁 *Descuento especial:* -${formatCOP(currentQuoteResult.discountAmount)}\n`;
            }
            text += `💰 *PRECIO TOTAL A PAGAR:* ${formatCOP(currentQuoteResult.finalPrice)} COP\n\n`;
            text += `✅ Oro 18K garantizado de por vida. Tejido personalizado.`;

            navigator.clipboard.writeText(text).then(() => {
                alert("¡Cotización copiada con el precio final! Ya puedes pegarla en WhatsApp.");
            }).catch(() => {
                prompt("Copia este texto para WhatsApp:", text);
            });
        }

        function handleProceedToInvoice() {
            if (!currentQuoteResult || currentQuoteResult.totalBeadsCount === 0) {
                alert("Por favor selecciona al menos 1 balín antes de crear la venta.");
                return;
            }
            if (!currentUser) {
                pendingTransferAfterLogin = true;
                openLoginModal();
                return;
            }
            executeTransferToInvoice();
        }

        function executeTransferToInvoice() {
            const summaryText = currentQuoteResult.breakdown.map(b => `${b.qty} de ${b.size}`).join(", ");
            pendingInvoiceItems = [{
                description: `Manilla con ${currentQuoteResult.totalBeadsCount} balines diamantados`,
                beads_details: JSON.stringify(quoteSelection),
                beads_summary: summaryText,
                cost_amount: currentQuoteResult.costBase,
                subtotal_beads: currentQuoteResult.subtotalProfit,
                surcharge: currentQuoteResult.surcharge,
                surcharge_rule: currentQuoteResult.isHighValue ? "+15% Manilla" : "+$50.000 Fijo",
                suggested_amount: currentQuoteResult.suggestedPrice,
                discount_amount: currentQuoteResult.discountAmount,
                total_price: currentQuoteResult.finalPrice,
                profit_amount: currentQuoteResult.netProfit
            }];
            renderInvoiceItems();
            switchTab('facturacion');
        }

        function updateInvoiceFinalPrice(newVal) {
            if (pendingInvoiceItems.length === 0) return;
            const item = pendingInvoiceItems[0];
            const parsed = Math.round(parseFloat(newVal) || 0);
            item.total_price = parsed;
            item.discount_amount = item.suggested_amount - parsed;
            item.profit_amount = parsed - item.cost_amount;
            renderInvoiceItems(false);
        }

        function renderInvoiceItems(updateInput = true) {
            const container = document.getElementById('invoice-items-table-container');
            const summary = document.getElementById('invoice-items-summary');
            const tbody = document.getElementById('invoice-items-tbody');

            if (pendingInvoiceItems.length === 0) {
                container.style.display = 'none';
                summary.style.display = 'block';
                document.getElementById('inv-summary-suggested').innerText = '$ 0';
                document.getElementById('inv-summary-discount').innerText = '$ 0';
                document.getElementById('inv-final-price-input').value = '';
                document.getElementById('inv-summary-profit').innerText = '$ 0';
                return;
            }
            summary.style.display = 'none';
            container.style.display = 'block';
            tbody.innerHTML = '';

            const item = pendingInvoiceItems[0];
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong>${item.description}</strong></td>
                <td><span class="badge-role badge-admin">${item.beads_summary}</span></td>
                <td style="color:var(--text-muted);">${formatCOP(item.suggested_amount)}</td>
                <td><strong style="color:var(--gold-bright); font-size:1.05rem;">${formatCOP(item.total_price)}</strong></td>
                <td style="color:var(--success); font-weight:700;">${formatCOP(item.profit_amount)}</td>
                <td><button class="btn btn-danger btn-sm" onclick="clearInvoiceForm()">✕</button></td>
            `;
            tbody.appendChild(tr);

            document.getElementById('inv-summary-suggested').innerText = formatCOP(item.suggested_amount);
            document.getElementById('inv-summary-discount').innerText = item.discount_amount >= 0
                ? `- ${formatCOP(item.discount_amount)}`
                : `+ ${formatCOP(Math.abs(item.discount_amount))}`;
            if (updateInput) {
                document.getElementById('inv-final-price-input').value = item.total_price;
            }
            document.getElementById('inv-summary-profit').innerText = formatCOP(item.profit_amount);
        }

        function clearInvoiceForm() {
            pendingInvoiceItems = [];
            renderInvoiceItems();
            document.getElementById('inv-customer-name').value = '';
            document.getElementById('inv-customer-phone').value = '';
            document.getElementById('inv-customer-id').value = '';
            document.getElementById('inv-notes').value = '';
        }

        async function submitInvoice() {
            const customerName = document.getElementById('inv-customer-name').value.trim();
            if (!customerName) return alert("Ingresa el nombre del cliente.");
            if (pendingInvoiceItems.length === 0) return alert("Carga una manilla desde el Cotizador antes de facturar.");

            const item = pendingInvoiceItems[0];
            const payload = {
                customer_name: customerName,
                customer_phone: document.getElementById('inv-customer-phone').value.trim(),
                customer_id_doc: document.getElementById('inv-customer-id').value.trim(),
                notes: document.getElementById('inv-notes').value.trim(),
                subtotal_beads: item.subtotal_beads,
                bracelet_surcharge: item.surcharge,
                surcharge_rule: item.surcharge_rule,
                suggested_amount: item.suggested_amount,
                discount_amount: item.discount_amount,
                total_amount: item.total_price,
                cost_amount: item.cost_amount,
                profit_amount: item.profit_amount,
                beads_details: item.beads_details
            };

            const res = await fetch('/api/invoices', {
                method: 'POST',
                headers: getAuthHeaders(),
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (!res.ok) return alert(data.error || "Error al crear factura");

            await saveServerToLocalMirror();
            clearInvoiceForm();
            resetQuote();
            await loadInventory();
            await loadSummaryStats();
            openInvoiceModal(data.invoice_id);
        }

        function renderInventoryTable() {
            const tbody = document.getElementById('inventory-tbody');
            if (!tbody) return;
            tbody.innerHTML = '';
            inventoryData.forEach(item => {
                const stock = item.stock || 0;
                const stockCostTotal = stock * (item.cost || 0);
                const unitProfit = (item.price_unit || 0) - (item.cost || 0);
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong style="font-size:1rem; color:var(--gold-light);">${item.size}</strong></td>
                    <td>
                        <div style="display:flex; align-items:center; gap:6px;">
                            <input type="number" id="inline-stock-${item.id}" value="${stock}" min="0" 
                                   class="form-control" style="width:78px; padding:6px 8px; font-weight:800; text-align:center;"
                                   onkeydown="if(event.key==='Enter') saveInlineStock(${item.id})">
                            <button class="btn btn-gold btn-sm" style="padding:6px 10px;" title="Guardar este stock" onclick="saveInlineStock(${item.id})">💾</button>
                        </div>
                        ${stock === 0 ? '<span style="color:var(--danger); font-size:0.75rem; font-weight:700;">Agotado</span>' : ''}
                    </td>
                    <td>${formatCOP(item.cost)}</td>
                    <td style="font-weight:700; color:var(--info);">${formatCOP(stockCostTotal)}</td>
                    <td><strong style="color:var(--gold-light);">${item.gain_unit_pct}%</strong></td>
                    <td style="color:var(--gold-bright); font-weight:700;">${formatCOP(item.price_unit)}</td>
                    <td style="color:var(--success); font-weight:700;">+${formatCOP(unitProfit)}</td>
                    <td>
                        <div style="display:flex; gap:6px;">
                            <button class="btn btn-gold btn-sm" onclick="openEditBeadModal(${item.id})">✏️ % Ganancia</button>
                            <button class="btn btn-secondary btn-sm" onclick="quickRestockPrompt('${item.size}')">+ Compra</button>
                        </div>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }

        async function saveInlineStock(itemId) {
            const input = document.getElementById('inline-stock-' + itemId);
            const newStock = parseInt(input.value);
            if (isNaN(newStock) || newStock < 0) {
                alert("Ingresa una cantidad válida.");
                return;
            }

            try {
                const res = await fetch('/api/inventory/set-stock', {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({ id: itemId, stock: newStock })
                });
                const data = await res.json();
                if (data.success) {
                    await saveServerToLocalMirror();
                    await loadInventory();
                    await loadSummaryStats();
                }
            } catch (err) {
                alert("Error al actualizar stock.");
            }
        }

        function openEditBeadModal(beadId) {
            const bead = inventoryData.find(b => b.id === beadId);
            if (!bead) return;

            document.getElementById('edit-bead-id').value = bead.id;
            document.getElementById('edit-bead-title').innerText = `Balín Diamantado ${bead.size}`;
            document.getElementById('edit-bead-cost').value = bead.cost;
            document.getElementById('edit-bead-gain-unit').value = bead.gain_unit_pct;
            document.getElementById('edit-bead-gain-x20').value = bead.gain_x20_pct;

            recalcBeadPreview();
            document.getElementById('edit-bead-modal').classList.add('active');
        }

        function closeEditBeadModal() {
            document.getElementById('edit-bead-modal').classList.remove('active');
        }

        function recalcBeadPreview() {
            const cost = parseFloat(document.getElementById('edit-bead-cost').value) || 0;
            const gainUnit = parseFloat(document.getElementById('edit-bead-gain-unit').value) || 0;
            const gainX20 = parseFloat(document.getElementById('edit-bead-gain-x20').value) || 0;

            const unitPrice = cost * (1.0 + (gainUnit / 100.0));
            const x20UnitPrice = cost * (1.0 + (gainX20 / 100.0));
            const x20Total = x20UnitPrice * 20;

            document.getElementById('edit-preview-unit-price').value = formatCOP(unitPrice);
            document.getElementById('edit-preview-x20-unit').value = formatCOP(x20UnitPrice);
            document.getElementById('edit-preview-x20-total').innerText = formatCOP(x20Total);
        }

        async function submitBeadMargins(e) {
            e.preventDefault();
            const beadId = parseInt(document.getElementById('edit-bead-id').value);
            const cost = parseFloat(document.getElementById('edit-bead-cost').value);
            const gainUnit = parseFloat(document.getElementById('edit-bead-gain-unit').value);
            const gainX20 = parseFloat(document.getElementById('edit-bead-gain-x20').value);

            try {
                const res = await fetch('/api/inventory/update-bead', {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({ id: beadId, cost, gain_unit_pct: gainUnit, gain_x20_pct: gainX20 })
                });
                const data = await res.json();
                if (data.success) {
                    await saveServerToLocalMirror();
                    closeEditBeadModal();
                    await loadInventory();
                    await loadSummaryStats();
                }
            } catch (err) {
                alert("Error al actualizar las ganancias.");
            }
        }

        function openBulkStockModal() {
            const container = document.getElementById('bulk-stock-inputs');
            container.innerHTML = '';
            inventoryData.forEach(item => {
                const div = document.createElement('div');
                div.innerHTML = `
                    <label style="display:block; font-size:0.8rem; color:var(--gold-light); margin-bottom:4px; font-weight:600;">
                        Balín ${item.size}:
                    </label>
                    <input type="number" min="0" id="bulk-input-${item.size.replace(' ', '')}" 
                           value="${item.stock || 0}" class="form-control" style="font-weight:700;">
                `;
                container.appendChild(div);
            });
            document.getElementById('bulk-stock-modal').classList.add('active');
        }

        function closeBulkStockModal() {
            document.getElementById('bulk-stock-modal').classList.remove('active');
        }

        async function submitBulkStock(e) {
            e.preventDefault();
            const stocksObj = {};
            inventoryData.forEach(item => {
                const val = parseInt(document.getElementById('bulk-input-' + item.size.replace(' ', '')).value) || 0;
                stocksObj[item.size] = val;
            });

            try {
                const res = await fetch('/api/inventory/set-stock', {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({ stocks: stocksObj })
                });
                const data = await res.json();
                if (data.success) {
                    await saveServerToLocalMirror();
                    closeBulkStockModal();
                    await loadInventory();
                    await loadSummaryStats();
                }
            } catch (err) {
                alert("Error al guardar inventario.");
            }
        }

        function quickRestockPrompt(size) {
            document.getElementById('restock-size').value = size;
            openRestockModal();
        }
        function openRestockModal() { document.getElementById('restock-modal').classList.add('active'); }
        function closeRestockModal() { document.getElementById('restock-modal').classList.remove('active'); }

        async function submitRestock(e) {
            e.preventDefault();
            const size = document.getElementById('restock-size').value;
            const qty = parseInt(document.getElementById('restock-qty').value);
            const recordExpense = document.getElementById('restock-record-expense').checked;

            await fetch('/api/inventory/add-stock', {
                method: 'POST',
                headers: getAuthHeaders(),
                body: JSON.stringify({ size, quantity: qty, record_expense: recordExpense })
            });
            await saveServerToLocalMirror();
            closeRestockModal();
            await loadInventory();
            await loadSummaryStats();
        }

        // ==================== CAJA & GASTOS ====================
        async function loadCashFlow() {
            try {
                const res = await fetch('/api/cashflow', { headers: getAuthHeaders() });
                if (!res.ok) return;
                const data = await res.json();
                document.getElementById('cash-total-in').innerText = formatCOP(data.total_income);
                document.getElementById('cash-total-out').innerText = formatCOP(data.total_expense);
                document.getElementById('cash-balance').innerText = formatCOP(data.balance);

                const tbody = document.getElementById('cash-flow-tbody');
                tbody.innerHTML = '';
                data.transactions.forEach(t => {
                    const isIngreso = t.type === 'INGRESO';
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td>${t.date}</td>
                        <td><span style="font-weight:700; color:${isIngreso ? 'var(--success)' : 'var(--danger)'};">${t.type}</span></td>
                        <td><strong>${t.category}</strong><br><small style="color:var(--text-muted);">${t.description}</small></td>
                        <td style="font-weight:700; color:${isIngreso ? 'var(--success)' : 'var(--danger)'};">${isIngreso ? '+' : '-'} ${formatCOP(t.amount)}</td>
                        <td><button class="btn btn-secondary btn-sm" onclick="deleteCashFlow(${t.id})">✕</button></td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (err) {}
        }

        async function submitCashFlow(e) {
            e.preventDefault();
            const payload = {
                type: document.getElementById('cf-type').value,
                category: document.getElementById('cf-category').value,
                description: document.getElementById('cf-desc').value.trim(),
                amount: parseFloat(document.getElementById('cf-amount').value),
                date: document.getElementById('cf-date').value || new Date().toISOString().split('T')[0]
            };
            await fetch('/api/cashflow', { method: 'POST', headers: getAuthHeaders(), body: JSON.stringify(payload) });
            await saveServerToLocalMirror();
            document.getElementById('cf-desc').value = '';
            document.getElementById('cf-amount').value = '';
            await loadCashFlow();
            await loadSummaryStats();
        }

        async function deleteCashFlow(id) {
            if (!confirm("¿Eliminar este movimiento de la cuenta?")) return;
            await fetch('/api/cashflow/' + id, { method: 'DELETE', headers: getAuthHeaders() });
            await saveServerToLocalMirror();
            await loadCashFlow();
            await loadSummaryStats();
        }

        // ==================== FACTURAS ====================
        async function loadInvoices() {
            try {
                const res = await fetch('/api/invoices', { headers: getAuthHeaders() });
                if (!res.ok) return;
                const list = await res.json();
                const tbody = document.getElementById('invoices-tbody');
                tbody.innerHTML = '';
                list.forEach(inv => {
                    const sug = inv.suggested_amount || inv.total_amount;
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong style="color:var(--gold-bright);">${inv.invoice_number}</strong></td>
                        <td>${inv.date}</td>
                        <td><strong>${inv.customer_name}</strong><br><small style="color:var(--text-muted);">${inv.customer_phone || ''}</small></td>
                        <td style="color:var(--text-muted);">${formatCOP(sug)}</td>
                        <td><strong style="color:var(--gold-light); font-size:1rem;">${formatCOP(inv.total_amount)}</strong></td>
                        <td style="color:var(--text-muted);">${formatCOP(inv.cost_amount)}</td>
                        <td style="color:var(--success); font-weight:800;">+${formatCOP(inv.profit_amount)}</td>
                        <td>
                            <div style="display:flex; gap:6px;">
                                <button class="btn btn-secondary btn-sm" onclick="openInvoiceModal(${inv.id})">🖨️ Ver</button>
                                <button class="btn btn-danger btn-sm" onclick="deleteInvoice(${inv.id})" title="Anular factura y devolver stock">✕</button>
                            </div>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (err) {}
        }

        async function deleteInvoice(invoiceId) {
            if (!confirm("¿Anular esta factura? Se devolverán los balines al inventario y se restará el ingreso de la caja.")) return;
            const res = await fetch('/api/invoices/' + invoiceId, { method: 'DELETE', headers: getAuthHeaders() });
            if (res.ok) {
                await saveServerToLocalMirror();
                await loadInvoices();
                await loadInventory();
                await loadSummaryStats();
            }
        }

        async function openInvoiceModal(invoiceId) {
            const res = await fetch('/api/invoices/' + invoiceId, { headers: getAuthHeaders() });
            const inv = await res.json();
            document.getElementById('modal-inv-number').innerText = `FACTURA DE VENTA N° ${inv.invoice_number}`;
            document.getElementById('modal-inv-customer').innerText = inv.customer_name;
            document.getElementById('modal-inv-phone').innerText = inv.customer_phone || 'N/A';
            document.getElementById('modal-inv-doc').innerText = inv.customer_id_doc || 'N/A';
            document.getElementById('modal-inv-date').innerText = inv.date;

            let detailsObj = {};
            try { detailsObj = JSON.parse(inv.beads_details || "{}"); } catch(e){}
            let beadListText = [];
            for (let sz in detailsObj) {
                if (detailsObj[sz] > 0) beadListText.push(`${detailsObj[sz]}x ${sz}`);
            }

            const tbody = document.getElementById('modal-inv-tbody');
            tbody.innerHTML = `
                <tr>
                    <td><strong>Manilla en Oro 18K con Balines Diamantados</strong></td>
                    <td>${beadListText.join(', ') || 'Manilla personalizada'}</td>
                    <td style="text-align:right; font-weight:600;">${formatCOP(inv.subtotal_beads)}</td>
                </tr>
            `;

            document.getElementById('modal-inv-subtotal').innerText = formatCOP(inv.subtotal_beads);
            document.getElementById('modal-inv-rule-label').innerText = `Confección (${inv.surcharge_rule}):`;
            document.getElementById('modal-inv-surcharge').innerText = formatCOP(inv.bracelet_surcharge);

            const discRow = document.getElementById('modal-inv-discount-row');
            if (inv.discount_amount && inv.discount_amount !== 0) {
                discRow.style.display = 'flex';
                document.getElementById('modal-inv-discount').innerText = inv.discount_amount > 0
                    ? `- ${formatCOP(inv.discount_amount)}`
                    : `+ ${formatCOP(Math.abs(inv.discount_amount))}`;
            } else {
                discRow.style.display = 'none';
            }

            document.getElementById('modal-inv-total').innerText = formatCOP(inv.total_amount);

            const notesBox = document.getElementById('modal-inv-notes-box');
            if (inv.notes) {
                notesBox.style.display = 'block';
                document.getElementById('modal-inv-notes').innerText = inv.notes;
            } else {
                notesBox.style.display = 'none';
            }

            document.getElementById('invoice-modal').classList.add('active');
        }

        function closeModal() {
            document.getElementById('invoice-modal').classList.remove('active');
        }

        // ==================== USUARIOS & COPIAS DE SEGURIDAD ====================
        async function downloadBackupFile() {
            const res = await fetch('/api/backup');
            const data = await res.json();
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `respaldo_joyeria_${new Date().toISOString().split('T')[0]}.json`;
            a.click();
        }

        async function uploadBackupFile(e) {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = async (ev) => {
                try {
                    const data = JSON.parse(ev.target.result);
                    const res = await fetch('/api/restore-mirror', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data)
                    });
                    if (res.ok) {
                        localStorage.setItem(MIRROR_STORAGE_KEY, JSON.stringify(data));
                        await reloadCurrentData();
                        alert("✅ Copia de seguridad restaurada con éxito.");
                    }
                } catch (err) {
                    alert("El archivo seleccionado no es válido.");
                }
            };
            reader.readAsText(file);
        }

        async function loadUsers() {
            try {
                const res = await fetch('/api/users', { headers: getAuthHeaders() });
                if (!res.ok) return;
                const users = await res.json();
                const tbody = document.getElementById('users-tbody');
                tbody.innerHTML = '';
                users.forEach(u => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong>${u.name}</strong></td>
                        <td>${u.email}</td>
                        <td><span class="badge-role badge-admin">${u.role}</span></td>
                        <td>
                            ${currentUser && u.email === currentUser.email ? '<span style="color:var(--text-muted); font-size:0.8rem;">(Activo)</span>' : `<button class="btn btn-danger btn-sm" onclick="deleteUser(${u.id})">Eliminar</button>`}
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (err) {}
        }

        async function handleCreateUser(e) {
            e.preventDefault();
            const name = document.getElementById('new-user-name').value.trim();
            const email = document.getElementById('new-user-email').value.trim();
            const password = document.getElementById('new-user-password').value;
            const role = document.getElementById('new-user-role').value;

            try {
                const res = await fetch('/api/users', {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({ name, email, password, role })
                });
                const data = await res.json();
                if (data.success) {
                    await saveServerToLocalMirror();
                    document.getElementById('new-user-name').value = '';
                    document.getElementById('new-user-email').value = '';
                    document.getElementById('new-user-password').value = '';
                    loadUsers();
                    alert("✅ Usuario creado exitosamente.");
                } else {
                    alert("Error: " + (data.error || "No se pudo crear."));
                }
            } catch (err) {
                alert("Error de conexión.");
            }
        }

        async function deleteUser(userId) {
            if (!confirm("¿Seguro que deseas eliminar este usuario?")) return;
            const res = await fetch('/api/users/' + userId, { method: 'DELETE', headers: getAuthHeaders() });
            if (res.ok) {
                await saveServerToLocalMirror();
                loadUsers();
            }
        }

        document.getElementById('cf-date').value = new Date().toISOString().split('T')[0];

        // Mantener despierto el servidor mientras la pestaña esté abierta
        setInterval(() => { fetch('/api/health').catch(() => {}); }, 240000);

        initApp();
    </script>
</body>
</html>
"""


class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class JewelryRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        if path == "/api/health":
            self._send_json({"status": "ok"})
            return

        # Ruta pública para el Cotizador sin necesidad de iniciar sesión
        if path == "/api/inventory":
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM beads_inventory ORDER BY id ASC")
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            self._send_json(rows)
            return

        if path == "/api/backup":
            self._send_json(export_full_backup())
            return

        # Rutas protegidas (requieren login)
        user = authenticate_request(self.headers)
        if not user:
            self._send_json({"error": "No autenticado"}, status=401)
            return

        if path == "/api/me":
            self._send_json({
                "id": user["id"],
                "email": user["email"],
                "name": user["name"],
                "role": user["role"]
            })
            return

        if path == "/api/summary":
            self._send_json(get_financial_and_inventory_summary())
            return

        if path == "/api/cashflow":
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM cash_flow ORDER BY date DESC, id DESC")
            transactions = [dict(r) for r in c.fetchall()]
            total_income = sum(t["amount"] for t in transactions if t["type"] == "INGRESO")
            total_expense = sum(t["amount"] for t in transactions if t["type"] == "SALIDA")
            balance = total_income - total_expense
            conn.close()
            self._send_json({
                "transactions": transactions,
                "total_income": total_income,
                "total_expense": total_expense,
                "balance": balance
            })
            return

        elif path == "/api/invoices":
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM invoices ORDER BY id DESC")
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            self._send_json(rows)
            return

        elif path.startswith("/api/invoices/"):
            inv_id = path.split("/")[-1]
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM invoices WHERE id = ?", (inv_id,))
            row = c.fetchone()
            conn.close()
            if row:
                self._send_json(dict(row))
            else:
                self._send_json({"error": "No encontrada"}, status=404)
            return

        elif path == "/api/users":
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT id, email, name, role FROM users ORDER BY id ASC")
            users = [dict(r) for r in c.fetchall()]
            conn.close()
            self._send_json(users)
            return

        self.send_error(404, "No encontrada")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

        # Restauración automática desde espejo local
        if path == "/api/restore-mirror":
            ok = import_full_backup(payload)
            sync_to_github_cloud()
            self._send_json({"success": ok})
            return

        # Login
        if path == "/api/login":
            email = payload.get("email", "").strip().lower()
            password = payload.get("password", "")
            pw_hash = hash_password(password)

            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT id, email, name, role, password_hash FROM users WHERE LOWER(email) = ?", (email,))
            user = c.fetchone()

            if user and user["password_hash"] == pw_hash:
                token = secrets.token_hex(24)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute("INSERT INTO sessions (token, user_id, role, created_at) VALUES (?, ?, ?, ?)",
                          (token, user["id"], "ADMIN", now_str))
                conn.commit()
                conn.close()
                self._send_json({
                    "success": True,
                    "token": token,
                    "user": {
                        "id": user["id"],
                        "email": user["email"],
                        "name": user["name"],
                        "role": "ADMIN"
                    }
                })
                return
            else:
                conn.close()
                self._send_json({"success": False, "error": "Correo o contraseña incorrectos."}, status=401)
                return

        # Rutas autenticadas
        user = authenticate_request(self.headers)
        if not user:
            self._send_json({"error": "Debes iniciar sesión para realizar esta acción."}, status=401)
            return

        if path == "/api/logout":
            token = self.headers.get("Authorization", "")[7:].strip()
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            conn.close()
            self._send_json({"success": True})
            return

        if path == "/api/invoices":
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as count FROM invoices")
            count = c.fetchone()["count"] + 1
            invoice_num = f"FACT-{count:04d}-{secrets.token_hex(1).upper()}"
            today_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            beads_details_raw = payload.get("beads_details", "{}")
            beads_details = json.loads(beads_details_raw) if isinstance(beads_details_raw, str) else beads_details_raw
            for size, qty in beads_details.items():
                if int(qty) > 0:
                    c.execute("UPDATE beads_inventory SET stock = MAX(0, stock - ?) WHERE size = ?", (int(qty), size))

            total_amount = float(payload.get("total_amount", 0))
            cost_amount = float(payload.get("cost_amount", 0))
            profit_amount = total_amount - cost_amount
            suggested_amount = float(payload.get("suggested_amount", total_amount))
            discount_amount = suggested_amount - total_amount

            c.execute("""
                INSERT INTO invoices (
                    invoice_number, date, customer_name, customer_phone, customer_id_doc,
                    subtotal_beads, bracelet_surcharge, surcharge_rule, suggested_amount,
                    discount_amount, total_amount, cost_amount, profit_amount, beads_details, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                invoice_num, today_str, payload.get("customer_name"),
                payload.get("customer_phone", ""), payload.get("customer_id_doc", ""),
                payload.get("subtotal_beads", 0), payload.get("bracelet_surcharge", 0),
                payload.get("surcharge_rule", ""), suggested_amount,
                discount_amount, total_amount, cost_amount, profit_amount,
                json.dumps(beads_details, ensure_ascii=False), payload.get("notes", "")
            ))
            invoice_id = c.lastrowid

            c.execute("""
                INSERT INTO cash_flow (date, type, category, description, amount, invoice_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().strftime("%Y-%m-%d"), "INGRESO", "Venta Manilla",
                f"Venta {invoice_num} - {payload.get('customer_name')} (Ganancia: ${round(profit_amount):,})",
                total_amount, invoice_id
            ))

            touch_updated_at(conn)
            conn.commit()
            conn.close()
            sync_to_github_cloud()
            self._send_json({"success": True, "invoice_id": invoice_id, "invoice_number": invoice_num})
            return

        elif path == "/api/inventory/update-bead":
            bead_id = payload.get("id")
            cost = float(payload.get("cost", 0))
            gain_unit_pct = float(payload.get("gain_unit_pct", 0))
            gain_x20_pct = float(payload.get("gain_x20_pct", 0))

            price_unit = round(cost * (1.0 + (gain_unit_pct / 100.0)))
            price_unit_x20 = round(cost * (1.0 + (gain_x20_pct / 100.0)))
            price_total_x20 = round(price_unit_x20 * 20)

            conn = get_db()
            c = conn.cursor()
            c.execute("""UPDATE beads_inventory 
                SET cost = ?, gain_unit_pct = ?, price_unit = ?, 
                    gain_x20_pct = ?, price_unit_x20 = ?, price_total_x20 = ?
                WHERE id = ?""", (cost, gain_unit_pct, price_unit, gain_x20_pct, price_unit_x20, price_total_x20, bead_id))
            touch_updated_at(conn)
            conn.commit()
            conn.close()
            sync_to_github_cloud()
            self._send_json({"success": True, "price_unit": price_unit, "price_total_x20": price_total_x20})
            return

        elif path == "/api/inventory/set-stock":
            conn = get_db()
            c = conn.cursor()
            if "stocks" in payload:
                for size, new_stock in payload["stocks"].items():
                    c.execute("UPDATE beads_inventory SET stock = ? WHERE size = ?", (int(new_stock), size))
            elif "id" in payload:
                c.execute("UPDATE beads_inventory SET stock = ? WHERE id = ?", (int(payload.get("stock", 0)), payload["id"]))
            elif "size" in payload:
                c.execute("UPDATE beads_inventory SET stock = ? WHERE size = ?", (int(payload.get("stock", 0)), payload["size"]))
            touch_updated_at(conn)
            conn.commit()
            conn.close()
            sync_to_github_cloud()
            self._send_json({"success": True})
            return

        elif path == "/api/inventory/add-stock":
            size = payload.get("size")
            qty = int(payload.get("quantity", 0))
            record_expense = payload.get("record_expense", False)

            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE beads_inventory SET stock = stock + ? WHERE size = ?", (qty, size))

            if record_expense:
                c.execute("SELECT cost FROM beads_inventory WHERE size = ?", (size,))
                row = c.fetchone()
                if row:
                    unit_cost = row["cost"]
                    c.execute("""
                        INSERT INTO cash_flow (date, type, category, description, amount)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        datetime.now().strftime("%Y-%m-%d"), "SALIDA", "Compra de Balines",
                        f"Compra de {qty} balines de {size}",
                        unit_cost * qty
                    ))

            touch_updated_at(conn)
            conn.commit()
            conn.close()
            sync_to_github_cloud()
            self._send_json({"success": True})
            return

        elif path == "/api/cashflow":
            conn = get_db()
            c = conn.cursor()
            c.execute("""
                INSERT INTO cash_flow (date, type, category, description, amount)
                VALUES (?, ?, ?, ?, ?)
            """, (
                payload.get("date"), payload.get("type"), payload.get("category"),
                payload.get("description"), payload.get("amount", 0)
            ))
            touch_updated_at(conn)
            conn.commit()
            conn.close()
            sync_to_github_cloud()
            self._send_json({"success": True})
            return

        elif path == "/api/users":
            name = payload.get("name", "").strip()
            email = payload.get("email", "").strip().lower()
            password = payload.get("password", "")
            role = "ADMIN"

            if not email or not password or not name:
                self._send_json({"error": "Faltan datos obligatorios"}, status=400)
                return

            pw_hash = hash_password(password)
            conn = get_db()
            c = conn.cursor()
            try:
                c.execute("""
                    INSERT INTO users (email, password_hash, name, role)
                    VALUES (?, ?, ?, ?)
                """, (email, pw_hash, name, role))
                touch_updated_at(conn)
                conn.commit()
                conn.close()
                sync_to_github_cloud()
                self._send_json({"success": True})
                return
            except sqlite3.IntegrityError:
                conn.close()
                self._send_json({"error": "Este correo ya se encuentra registrado."}, status=400)
                return

        self.send_error(404, "No encontrada")

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        user = authenticate_request(self.headers)
        if not user:
            self._send_json({"error": "Acceso denegado"}, status=403)
            return

        if path.startswith("/api/cashflow/"):
            flow_id = path.split("/")[-1]
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM cash_flow WHERE id = ?", (flow_id,))
            touch_updated_at(conn)
            conn.commit()
            conn.close()
            sync_to_github_cloud()
            self._send_json({"success": True})
            return

        elif path.startswith("/api/invoices/"):
            inv_id = int(path.split("/")[-1])
            conn = get_db()
            c = conn.cursor()
            inv = c.execute("SELECT * FROM invoices WHERE id = ?", (inv_id,)).fetchone()
            if inv:
                try:
                    details = json.loads(inv["beads_details"] or "{}")
                    for sz, q in details.items():
                        if int(q) > 0:
                            c.execute("UPDATE beads_inventory SET stock = stock + ? WHERE size = ?", (int(q), sz))
                except Exception:
                    pass
                c.execute("DELETE FROM cash_flow WHERE invoice_id = ?", (inv_id,))
                c.execute("DELETE FROM invoices WHERE id = ?", (inv_id,))
                touch_updated_at(conn)
                conn.commit()
            conn.close()
            sync_to_github_cloud()
            self._send_json({"success": True})
            return

        elif path.startswith("/api/users/"):
            del_user_id = int(path.split("/")[-1])
            if del_user_id == user["id"]:
                self._send_json({"error": "No puedes eliminar tu propio usuario activo."}, status=400)
                return

            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM users WHERE id = ?", (del_user_id,))
            touch_updated_at(conn)
            conn.commit()
            conn.close()
            sync_to_github_cloud()
            self._send_json({"success": True})
            return

        self.send_error(404, "No encontrada")

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def start_server():
    with ThreadingHTTPServer(("", PORT), JewelryRequestHandler) as httpd:
        url = f"http://localhost:{PORT}"
        print("=" * 65)
        print("  ✨ JOYERÍA ORO & BALINES 18K - SERVIDOR MULTIHILO ACTIVO ✨")
        print(f"  URL: {url}")
        print("=" * 65)
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor detenido.")
            httpd.server_close()


if __name__ == "__main__":
    start_server()    
