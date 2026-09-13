#!/usr/bin/env python3
"""
SISTEMA DE GESTIÓN, COTIZACIÓN Y FACTURACIÓN
MANILLAS CON BALINES DIAMANTADOS EN ORO
Con autenticación multi-usuario y roles (ADMIN vs COTIZADOR)
Ejecución: python app_joyeria.py
"""

import http.server
import socketserver
import json
import sqlite3
import urllib.parse
import webbrowser
import hashlib
import secrets
import os
import sys
import tempfile
from datetime import datetime

PORT = int(os.environ.get("PORT", 8000))

def get_db_path():
    local_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(local_dir, "joyeria_balines.db")
    try:
        conn = sqlite3.connect(local_path)
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
            role TEXT -- 'ADMIN' o 'COTIZADOR'
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
            total_amount REAL,
            cost_amount REAL,
            profit_amount REAL,
            notes TEXT
        )
    """)
    
    # 6. Tabla de detalles de la factura
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER,
            item_type TEXT,
            description TEXT,
            beads_details TEXT,
            quantity INTEGER,
            unit_price REAL,
            total_price REAL,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id)
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

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Joyería Balines de Oro - Sistema de Gestión</title>
    <style>
        :root {
            --gold-primary: #D4AF37;
            --gold-light: #F3E5AB;
            --gold-dark: #AA7C11;
            --bg-main: #0B0F19;
            --bg-card: #151D2E;
            --bg-card-alt: #1E293B;
            --text-main: #F8FAFC;
            --text-muted: #94A3B8;
            --border-color: #334155;
            --success: #10B981;
            --danger: #EF4444;
            --info: #38BDF8;
            --shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; }
        body { background-color: var(--bg-main); color: var(--text-main); line-height: 1.5; padding-bottom: 60px; }
        
        header { 
            background: linear-gradient(135deg, #111827 0%, #1e1b4b 100%); 
            border-bottom: 2px solid var(--gold-primary); 
            padding: 1rem 1.5rem; 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            flex-wrap: wrap;
            gap: 10px;
        }
        .header-brand h1 { font-size: 1.4rem; color: var(--gold-primary); letter-spacing: 0.5px; }
        .header-user { display: flex; align-items: center; gap: 10px; font-size: 0.85rem; }
        .badge-role { 
            padding: 4px 10px; 
            border-radius: 20px; 
            font-weight: 700; 
            font-size: 0.75rem; 
            text-transform: uppercase;
        }
        .badge-admin { background: rgba(212, 175, 55, 0.2); color: var(--gold-primary); border: 1px solid var(--gold-primary); }
        .badge-cotizador { background: rgba(56, 189, 248, 0.2); color: var(--info); border: 1px solid var(--info); }

        nav { 
            display: flex; 
            gap: 8px; 
            background: var(--bg-card); 
            padding: 8px 1.5rem; 
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
            border: none; 
            padding: 8px 14px; 
            border-radius: 8px; 
            cursor: pointer; 
            font-size: 0.9rem; 
            font-weight: 600; 
            transition: all 0.2s; 
            flex-shrink: 0;
        }
        .nav-btn:hover { color: var(--gold-primary); background: rgba(212, 175, 55, 0.08); }
        .nav-btn.active { background: var(--gold-primary); color: #000; }

        .container { max-width: 1200px; margin: 20px auto; padding: 0 16px; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: var(--shadow); }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; border-bottom: 1px solid var(--border-color); padding-bottom: 10px; flex-wrap: wrap; gap: 10px; }
        .card-header h2 { font-size: 1.2rem; color: var(--gold-primary); }
        
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
        .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
        @media (max-width: 850px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }

        .form-group { margin-bottom: 14px; }
        .form-group label { display: block; margin-bottom: 5px; font-size: 0.8rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; }
        .form-control { width: 100%; padding: 10px 12px; background: var(--bg-card-alt); border: 1px solid var(--border-color); border-radius: 8px; color: #fff; font-size: 0.95rem; }
        .form-control:focus { outline: none; border-color: var(--gold-primary); }
        
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px 16px; border-radius: 8px; border: none; font-size: 0.92rem; font-weight: 600; cursor: pointer; transition: all 0.2s; }
        .btn-gold { background: var(--gold-primary); color: #000; }
        .btn-gold:hover { background: var(--gold-dark); color: #fff; }
        .btn-secondary { background: var(--bg-card-alt); color: var(--text-main); border: 1px solid var(--border-color); }
        .btn-danger { background: var(--danger); color: #fff; }
        .btn-sm { padding: 5px 10px; font-size: 0.8rem; }

        .table-responsive { overflow-x: auto; -webkit-overflow-scrolling: touch; }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th { background: var(--bg-card-alt); color: var(--gold-light); font-size: 0.8rem; text-transform: uppercase; padding: 10px 12px; border-bottom: 1px solid var(--border-color); }
        td { padding: 10px 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); font-size: 0.9rem; }

        .stat-card { background: var(--bg-card-alt); border: 1px solid var(--border-color); border-radius: 10px; padding: 15px; border-left: 4px solid var(--gold-primary); }
        .stat-card.success-border { border-left-color: var(--success); }
        .stat-card.danger-border { border-left-color: var(--danger); }
        .stat-label { font-size: 0.78rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }
        .stat-value { font-size: 1.45rem; font-weight: 700; color: #fff; margin-top: 4px; }

        .bead-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.06); gap: 10px; }
        .bead-counter { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
        .bead-counter button { width: 32px; height: 32px; border-radius: 6px; border: 1px solid var(--border-color); background: var(--bg-card-alt); color: #fff; font-size: 1.1rem; cursor: pointer; }
        .bead-counter input { width: 45px; height: 32px; text-align: center; background: var(--bg-main); border: 1px solid var(--border-color); border-radius: 6px; color: #fff; font-weight: 700; }

        .rule-badge { background: rgba(56, 189, 248, 0.1); border: 1px solid var(--info); color: var(--info); padding: 10px 12px; border-radius: 8px; font-size: 0.85rem; margin: 12px 0; }
        .rule-badge.high-value { background: rgba(212, 175, 55, 0.15); border-color: var(--gold-primary); color: var(--gold-light); }

        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); backdrop-filter: blur(4px); z-index: 1000; justify-content: center; align-items: center; padding: 15px; }
        .modal-overlay.active { display: flex; }
        .modal-content { background: #fff; color: #1a1a1a; width: 100%; max-width: 580px; max-height: 92vh; overflow-y: auto; border-radius: 10px; padding: 22px; box-shadow: 0 20px 40px rgba(0,0,0,0.6); }

        /* Pantalla de Login */
        #auth-screen {
            min-height: 80vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .login-box {
            background: var(--bg-card);
            border: 2px solid var(--gold-primary);
            border-radius: 16px;
            padding: 35px 28px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.7);
            text-align: center;
        }
        .login-box h2 {
            color: var(--gold-primary);
            font-size: 1.5rem;
            margin-bottom: 6px;
        }

        /* Factura Impresión */
        .inv-header { text-align: center; border-bottom: 2px dashed #ccc; padding-bottom: 10px; margin-bottom: 10px; }
        .inv-table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.88rem; }
        .inv-table th { background: #f8fafc; color: #334155; padding: 6px; border-bottom: 2px solid #cbd5e1; }
        .inv-table td { padding: 6px; border-bottom: 1px solid #f1f5f9; color: #1e293b; }
        .inv-totals { margin-left: auto; width: 65%; margin-top: 8px; }
        .inv-totals-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 0.88rem; }
        .inv-totals-row.grand-total { border-top: 2px solid #334155; padding-top: 6px; font-size: 1.1rem; font-weight: 700; color: #b45309; }
        
        @media print {
            body * { visibility: hidden; }
            .modal-content, .modal-content * { visibility: visible; }
            .modal-overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: white; padding: 0; }
            .no-print { display: none !important; }
        }
    </style>
</head>
<body>

    <!-- ==================== PANTALLA DE LOGIN ==================== -->
    <div id="auth-screen">
        <div class="login-box">
            <div style="font-size: 2.5rem; margin-bottom: 10px;">✨</div>
            <h2>JOYERÍA ORO & BALINES</h2>
            <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 25px;">Acceso seguro exclusivo para el equipo</p>

            <div id="login-error-box" style="display:none; background: rgba(239, 68, 68, 0.2); border: 1px solid var(--danger); color: var(--danger); padding: 8px; border-radius: 6px; font-size: 0.85rem; margin-bottom: 15px;"></div>

            <form onsubmit="handleLogin(event)" style="text-align: left;">
                <div class="form-group">
                    <label>Correo Electrónico</label>
                    <input type="email" id="login-email" class="form-control" placeholder="ejemplo@joyeria.com" required autocomplete="username">
                </div>
                <div class="form-group">
                    <label>Contraseña</label>
                    <input type="password" id="login-password" class="form-control" placeholder="••••••••" required autocomplete="current-password">
                </div>
                <button type="submit" class="btn btn-gold" style="width: 100%; margin-top: 10px; font-size: 1rem; padding: 12px;">
                    🔑 Iniciar Sesión
                </button>
            </form>

            <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid var(--border-color); font-size: 0.78rem; color: var(--text-muted); text-align: left;">
                <strong>Cuentas configuradas:</strong><br>
                👑 <strong>Admin (Total):</strong> <code>admin@joyeria.com</code> / <code>admin123</code><br>
                👤 <strong>Vendedor (Cotizador):</strong> <code>vendedor@joyeria.com</code> / <code>cotizar123</code>
            </div>
        </div>
    </div>

    <!-- ==================== APLICACIÓN PRINCIPAL ==================== -->
    <div id="app-screen" style="display:none;">
        <header>
            <div class="header-brand">
                <h1>JOYERÍA ORO & BALINES</h1>
            </div>
            <div class="header-user">
                <span id="user-display-badge" class="badge-role badge-admin">ADMIN</span>
                <span id="user-display-email" style="color: var(--text-muted); font-size: 0.82rem;">correo@joyeria.com</span>
                <button class="btn btn-secondary btn-sm" onclick="handleLogout()" title="Cerrar sesión">Salir</button>
            </div>
        </header>

        <nav id="main-nav">
            <button class="nav-btn active" onclick="switchTab('cotizador')">📿 Cotizador</button>
            <!-- Pestañas exclusivas de Administrador -->
            <button class="nav-btn admin-only" onclick="switchTab('facturacion')">🧾 Facturación</button>
            <button class="nav-btn admin-only" onclick="switchTab('inventario')">📦 Inventario</button>
            <button class="nav-btn admin-only" onclick="switchTab('caja')">💰 Caja & Gastos</button>
            <button class="nav-btn admin-only" onclick="switchTab('historial')">📑 Facturas</button>
            <button class="nav-btn admin-only" onclick="switchTab('usuarios')">👥 Usuarios</button>
            <button class="btn btn-secondary btn-sm" style="margin-left:auto;" onclick="reloadCurrentData()" title="Actualizar datos">🔄 Refrescar</button>
        </nav>

        <div class="container">
            <!-- ==================== TAB 1: COTIZADOR ==================== -->
            <div id="tab-cotizador" class="tab-content active">
                <div class="grid-2">
                    <div class="card">
                        <div class="card-header">
                            <h2>🔘 Diseñar Manilla</h2>
                            <button class="btn btn-secondary btn-sm" onclick="resetQuote()">Limpiar</button>
                        </div>
                        <div id="quote-beads-list"></div>
                    </div>

                    <div class="card">
                        <div class="card-header">
                            <h2>📊 Valor de la Manilla</h2>
                        </div>
                        <div class="stat-card" style="margin-bottom: 12px;">
                            <div class="stat-label">Precio Final Sugerido al Cliente</div>
                            <div class="stat-value" id="quote-total-display">$ 0</div>
                            <div style="font-size: 0.8rem; color: var(--text-muted);" id="quote-sub-display">0 balines seleccionados</div>
                        </div>

                        <div id="quote-rule-alert" class="rule-badge">
                            ℹ️ Selecciona balines para calcular la tarifa de confección.
                        </div>

                        <div style="background: var(--bg-card-alt); border-radius: 8px; padding: 14px; margin-bottom: 16px;">
                            <div style="display:flex; justify-content:space-between; font-size:0.9rem; margin-bottom:6px;">
                                <span style="color:var(--text-muted);">Subtotal Balines:</span>
                                <span id="quote-beads-subtotal" style="font-weight:600;">$ 0</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; font-size:0.9rem; margin-bottom:6px;">
                                <span style="color:var(--gold-light);">Tarifa Confección / Manilla:</span>
                                <span id="quote-surcharge-val" style="color:var(--gold-primary); font-weight:700;">$ 0</span>
                            </div>

                            <!-- Desglose de costo y ganancia visible para Cotizador y Admin -->
                            <div style="border-top:1px dashed var(--border-color); padding-top:8px; margin-top:8px;">
                                <div style="display:flex; justify-content:space-between; font-size:0.85rem; margin-bottom:4px; color:var(--text-muted);">
                                    <span>Costo Inicial Materiales (Inversión):</span>
                                    <span id="quote-cost-base">$ 0</span>
                                </div>
                                <div style="display:flex; justify-content:space-between; font-size:1rem; font-weight:700; color:var(--success);">
                                    <span>Ganancia Neta Total:</span>
                                    <span id="quote-profit-val">$ 0</span>
                                </div>
                            </div>
                        </div>

                        <div style="display:flex; flex-direction:column; gap:8px;">
                            <button class="btn btn-secondary" onclick="copyQuoteForWhatsApp()">
                                📋 Copiar Resumen para WhatsApp
                            </button>
                            <button class="btn btn-gold admin-only" onclick="transferQuoteToInvoice()">
                                ✨ Facturar esta Manilla Directamente
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ==================== TAB 2: FACTURACIÓN (ADMIN) ==================== -->
            <div id="tab-facturacion" class="tab-content admin-only">
                <div class="card">
                    <div class="card-header">
                        <h2>🧾 Factura de Venta</h2>
                    </div>
                    <div class="grid-3">
                        <div class="form-group">
                            <label>Nombre del Cliente</label>
                            <input type="text" id="inv-customer-name" class="form-control" placeholder="Ej: Dylan Nicolas" required>
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

                    <div style="background: var(--bg-card-alt); border-radius: 8px; padding: 14px; margin: 15px 0;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                            <h4 style="color: var(--gold-light);">ÍTEMS A FACTURAR</h4>
                            <button class="btn btn-secondary btn-sm" onclick="switchTab('cotizador')">+ Cargar Cotización</button>
                        </div>
                        <div id="invoice-items-summary" style="color: var(--text-muted); font-size:0.9rem;">
                            No hay manillas cargadas. Pasa al cotizador para cargar una manilla.
                        </div>
                        <div id="invoice-items-table-container" style="display:none;" class="table-responsive">
                            <table>
                                <thead>
                                    <tr><th>Descripción</th><th>Detalle Balines</th><th>Regla Aplicada</th><th>Total</th><th></th></tr>
                                </thead>
                                <tbody id="invoice-items-tbody"></tbody>
                            </table>
                        </div>
                    </div>

                    <div class="grid-2">
                        <div class="form-group">
                            <label>Observaciones (Color hilo, medida, etc.)</label>
                            <textarea id="inv-notes" class="form-control" rows="3" placeholder="Ej: Hilo negro macramé, medida 17cm..."></textarea>
                        </div>
                        <div style="background: var(--bg-card-alt); padding: 14px; border-radius: 8px;">
                            <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                                <span>Subtotal Balines:</span><span id="inv-summary-beads">$ 0</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
                                <span>Confección Manilla:</span><span id="inv-summary-surcharge">$ 0</span>
                            </div>
                            <div style="display:flex; justify-content:space-between; font-size:1.3rem; font-weight:700; color:var(--gold-primary); border-top:1px solid var(--border-color); padding-top:6px;">
                                <span>TOTAL FACTURA:</span><span id="inv-summary-total">$ 0</span>
                            </div>
                        </div>
                    </div>

                    <div style="margin-top: 18px; display: flex; gap: 12px; flex-wrap:wrap;">
                        <button class="btn btn-gold" style="flex: 2; min-width:200px;" onclick="submitInvoice()">
                            💾 Emitir Factura y Descontar Balines
                        </button>
                        <button class="btn btn-secondary" style="flex: 1;" onclick="clearInvoiceForm()">Limpiar</button>
                    </div>
                </div>
            </div>

            <!-- ==================== TAB 3: INVENTARIO (ADMIN) ==================== -->
            <div id="tab-inventario" class="tab-content admin-only">
                <div class="card">
                    <div class="card-header">
                        <h2>📦 Control de Inventario de Balines</h2>
                        <div style="display:flex; gap:10px; flex-wrap:wrap;">
                            <button class="btn btn-gold btn-sm" onclick="openBulkStockModal()">✏️ Ajustar Conteo Real (Todos)</button>
                            <button class="btn btn-secondary btn-sm" onclick="openRestockModal()">+ Registrar Compra / Entrada</button>
                        </div>
                    </div>
                    <p style="color:var(--text-muted); font-size:0.85rem; margin-bottom:15px;">
                        💡 Edita el stock directamente en cada casilla y pulsa <strong>💾</strong>. También puedes cambiar costos y <strong>% de ganancia</strong> con el botón <strong>"✏️ % Ganancia"</strong>.
                    </p>
                    <div class="table-responsive">
                        <table>
                            <thead>
                                <tr>
                                    <th>Tamaño</th>
                                    <th style="min-width:130px;">Stock Real</th>
                                    <th>Costo Inicial</th>
                                    <th>% Gan. Unit</th>
                                    <th>Venta Unit.</th>
                                    <th>% Gan. X20</th>
                                    <th>Venta Unit. X20</th>
                                    <th>Total X20</th>
                                    <th>Acciones</th>
                                </tr>
                            </thead>
                            <tbody id="inventory-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- ==================== TAB 4: FLUJO DE CAJA (ADMIN) ==================== -->
            <div id="tab-caja" class="tab-content admin-only">
                <div class="grid-3" style="margin-bottom: 20px;">
                    <div class="stat-card success-border">
                        <div class="stat-label">Total Ingresos (Ventas)</div>
                        <div class="stat-value" id="cash-total-in" style="color:var(--success);">$ 0</div>
                    </div>
                    <div class="stat-card danger-border">
                        <div class="stat-label">Total Salidas (Gastos)</div>
                        <div class="stat-value" id="cash-total-out" style="color:var(--danger);">$ 0</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Balance Neto</div>
                        <div class="stat-value" id="cash-balance" style="color:var(--gold-primary);">$ 0</div>
                    </div>
                </div>

                <div class="grid-2">
                    <div class="card">
                        <div class="card-header"><h2>💸 Registrar Gasto / Ingreso</h2></div>
                        <form onsubmit="submitCashFlow(event)">
                            <div class="grid-2">
                                <div class="form-group">
                                    <label>Tipo</label>
                                    <select id="cf-type" class="form-control" required>
                                        <option value="SALIDA">SALIDA (Gasto)</option>
                                        <option value="INGRESO">INGRESO (Entrada)</option>
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
                                        <option value="Otro">Otro Gasto</option>
                                    </select>
                                </div>
                            </div>
                            <div class="form-group">
                                <label>Descripción</label>
                                <input type="text" id="cf-desc" class="form-control" placeholder="Ej: Hilos terlenca para tejido" required>
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
                            <button type="submit" class="btn btn-gold" style="width:100%;">Registrar en Caja</button>
                        </form>
                    </div>

                    <div class="card">
                        <div class="card-header"><h2>📋 Movimientos Recientes</h2></div>
                        <div class="table-responsive" style="max-height: 400px; overflow-y: auto;">
                            <table>
                                <thead><tr><th>Fecha</th><th>Tipo</th><th>Detalle</th><th>Monto</th><th></th></tr></thead>
                                <tbody id="cash-flow-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- ==================== TAB 5: HISTORIAL FACTURAS (ADMIN) ==================== -->
            <div id="tab-historial" class="tab-content admin-only">
                <div class="card">
                    <div class="card-header"><h2>📑 Facturas Emitidas</h2></div>
                    <div class="table-responsive">
                        <table>
                            <thead>
                                <tr><th>Factura</th><th>Fecha</th><th>Cliente</th><th>Teléfono</th><th>Total</th><th>Ganancia</th><th>Acción</th></tr>
                            </thead>
                            <tbody id="invoices-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- ==================== TAB 6: GESTIÓN DE USUARIOS (ADMIN) ==================== -->
            <div id="tab-usuarios" class="tab-content admin-only">
                <div class="grid-2">
                    <div class="card">
                        <div class="card-header">
                            <h2>➕ Crear Nuevo Usuario</h2>
                        </div>
                        <form onsubmit="handleCreateUser(event)">
                            <div class="form-group">
                                <label>Nombre de la Persona</label>
                                <input type="text" id="new-user-name" class="form-control" placeholder="Ej: Harold Itanare" required>
                            </div>
                            <div class="form-group">
                                <label>Correo Electrónico (Para Iniciar Sesión)</label>
                                <input type="email" id="new-user-email" class="form-control" placeholder="ejemplo@joyeria.com" required>
                            </div>
                            <div class="form-group">
                                <label>Contraseña</label>
                                <input type="password" id="new-user-password" class="form-control" placeholder="Mínimo 6 caracteres" required>
                            </div>
                            <div class="form-group">
                                <label>Rol / Permiso</label>
                                <select id="new-user-role" class="form-control" required>
                                    <option value="COTIZADOR">👤 COTIZADOR (Solo puede cotizar manillas)</option>
                                    <option value="ADMIN">👑 ADMINISTRADOR (Acceso y modificación total)</option>
                                </select>
                            </div>
                            <button type="submit" class="btn btn-gold" style="width:100%;">
                                Guardar Usuario
                            </button>
                        </form>
                    </div>

                    <div class="card">
                        <div class="card-header">
                            <h2>👥 Usuarios Registrados</h2>
                        </div>
                        <div class="table-responsive">
                            <table>
                                <thead>
                                    <tr><th>Nombre</th><th>Correo</th><th>Rol</th><th>Acción</th></tr>
                                </thead>
                                <tbody id="users-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

        </div>
    </div>

    <!-- ==================== MODALES ==================== -->

    <!-- MODAL IMPRIMIR FACTURA -->
    <div id="invoice-modal" class="modal-overlay">
        <div class="modal-content">
            <div class="inv-header">
                <h2>✨ JOYERÍA FINA ✨</h2>
                <p style="font-weight:600; color:#555;">VENTA DE MANILLAS CON BALINES DIAMANTADOS EN ORO</p>
                <p style="font-size:0.8rem; color:#777;">Calidad Certificada • Oro 18K</p>
                <div style="margin-top:6px; font-weight:700; color:#b45309;" id="modal-inv-number">FACTURA N°</div>
            </div>
            <div class="grid-2" style="font-size:0.88rem; margin-bottom:10px;">
                <div><p><strong>Cliente:</strong> <span id="modal-inv-customer"></span></p><p><strong>Tel:</strong> <span id="modal-inv-phone"></span></p></div>
                <div style="text-align:right;"><p><strong>Fecha:</strong> <span id="modal-inv-date"></span></p><p><strong>Doc:</strong> <span id="modal-inv-doc"></span></p></div>
            </div>
            <table class="inv-table">
                <thead><tr><th>Descripción</th><th>Balines</th><th style="text-align:right;">Total</th></tr></thead>
                <tbody id="modal-inv-tbody"></tbody>
            </table>
            <div class="inv-totals">
                <div class="inv-totals-row"><span>Subtotal Balines:</span><span id="modal-inv-subtotal">$ 0</span></div>
                <div class="inv-totals-row"><span id="modal-inv-rule-label">Confección:</span><span id="modal-inv-surcharge">$ 0</span></div>
                <div class="inv-totals-row grand-total"><span>TOTAL PAGADO:</span><span id="modal-inv-total">$ 0</span></div>
            </div>
            <div id="modal-inv-notes-box" style="background:#f8fafc; padding:8px; border-radius:5px; font-size:0.82rem; margin-top:10px;">
                <strong>Notas:</strong> <span id="modal-inv-notes"></span>
            </div>
            <div style="display:flex; gap:10px; margin-top:18px;" class="no-print">
                <button class="btn btn-gold" style="flex:1;" onclick="window.print()">🖨️ Imprimir / Guardar PDF</button>
                <button class="btn btn-secondary" style="flex:1;" onclick="closeModal()">Cerrar</button>
            </div>
        </div>
    </div>

    <!-- MODAL EDITAR GANANCIAS Y COSTOS -->
    <div id="edit-bead-modal" class="modal-overlay">
        <div class="modal-content" style="background:var(--bg-card); color:#fff; max-width:480px;">
            <div class="card-header">
                <h3 style="color:var(--gold-primary);">✏️ Modificar Porcentajes de Ganancia</h3>
            </div>
            <form onsubmit="submitBeadMargins(event)" style="margin-top:15px;">
                <input type="hidden" id="edit-bead-id">
                
                <div style="background:var(--bg-card-alt); padding:10px 14px; border-radius:8px; margin-bottom:15px;">
                    <span style="font-size:0.85rem; color:var(--text-muted);">Balín seleccionado:</span>
                    <h3 id="edit-bead-title" style="color:var(--gold-light); margin-top:2px;">Balín 3 mm</h3>
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
                        <input type="text" id="edit-preview-unit-price" class="form-control" style="background:#0f172a; color:var(--gold-primary); font-weight:700;" readonly>
                    </div>
                </div>

                <div class="grid-2">
                    <div class="form-group">
                        <label>% Ganancia x20 (Por Mayor)</label>
                        <input type="number" id="edit-bead-gain-x20" class="form-control" min="0" max="500" step="0.5" oninput="recalcBeadPreview()" required>
                    </div>
                    <div class="form-group">
                        <label>Precio Unitario x20</label>
                        <input type="text" id="edit-preview-x20-unit" class="form-control" style="background:#0f172a; color:var(--text-main);" readonly>
                    </div>
                </div>

                <div style="background:var(--bg-card-alt); padding:10px 14px; border-radius:8px; margin: 10px 0 20px 0; display:flex; justify-content:space-between; align-items:center;">
                    <span style="font-size:0.85rem; color:var(--text-muted);">Precio Total Paquete x20:</span>
                    <strong id="edit-preview-x20-total" style="color:var(--gold-primary); font-size:1.1rem;">$ 0</strong>
                </div>

                <div style="display:flex; gap:10px;">
                    <button type="submit" class="btn btn-gold" style="flex:1;">💾 Guardar Ganancias</button>
                    <button type="button" class="btn btn-secondary" style="flex:1;" onclick="closeEditBeadModal()">Cancelar</button>
                </div>
            </form>
        </div>
    </div>

    <!-- MODAL AJUSTAR TODO EL INVENTARIO -->
    <div id="bulk-stock-modal" class="modal-overlay">
        <div class="modal-content" style="background:var(--bg-card); color:#fff; max-width:480px;">
            <div class="card-header">
                <h3 style="color:var(--gold-primary);">✏️ Ajustar Conteo Físico Real</h3>
            </div>
            <p style="font-size:0.85rem; color:var(--text-muted); margin: 10px 0 15px 0;">
                Digita las cantidades exactas que tienes actualmente en tu taller/inventario físico:
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
        <div class="modal-content" style="background:var(--bg-card); color:#fff; max-width:440px;">
            <div class="card-header"><h3 style="color:var(--gold-primary);">📦 Entrada de Balines (Compra)</h3></div>
            <form onsubmit="submitRestock(event)">
                <div class="form-group">
                    <label>Tamaño</label>
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
                    <label>Cantidad de Unidades Nuevas</label>
                    <input type="number" id="restock-qty" class="form-control" min="1" placeholder="Ej: 20" required>
                </div>
                <div class="form-group" style="display:flex; align-items:center; gap:8px;">
                    <input type="checkbox" id="restock-record-expense" checked>
                    <label for="restock-record-expense" style="margin:0; text-transform:none; color:var(--gold-light);">
                        Registrar gasto automáticamente en Flujo de Caja
                    </label>
                </div>
                <div style="display:flex; gap:10px; margin-top:15px;">
                    <button type="submit" class="btn btn-gold" style="flex:1;">Guardar Entrada</button>
                    <button type="button" class="btn btn-secondary" style="flex:1;" onclick="closeRestockModal()">Cancelar</button>
                </div>
            </form>
        </div>
    </div>

    <!-- ==================== JAVASCRIPT ==================== -->
    <script>
        let currentUser = null;
        let inventoryData = [];
        let quoteSelection = {};
        let currentQuoteResult = null;
        let pendingInvoiceItems = [];

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

        // ==================== AUTENTICACIÓN ====================
        async function checkAuth() {
            const token = localStorage.getItem('auth_token');
            if (!token) {
                showLoginScreen();
                return;
            }

            try {
                const res = await fetch('/api/me', { headers: getAuthHeaders() });
                if (res.ok) {
                    currentUser = await res.json();
                    showAppScreen();
                } else {
                    localStorage.removeItem('auth_token');
                    showLoginScreen();
                }
            } catch (err) {
                showLoginScreen();
            }
        }

        function showLoginScreen() {
            document.getElementById('auth-screen').style.display = 'flex';
            document.getElementById('app-screen').style.display = 'none';
        }

        function showAppScreen() {
            document.getElementById('auth-screen').style.display = 'none';
            document.getElementById('app-screen').style.display = 'block';

            document.getElementById('user-display-email').innerText = currentUser.email;
            const roleBadge = document.getElementById('user-display-badge');
            roleBadge.innerText = currentUser.role;
            if (currentUser.role === 'ADMIN') {
                roleBadge.className = 'badge-role badge-admin';
            } else {
                roleBadge.className = 'badge-role badge-cotizador';
            }

            const adminElements = document.querySelectorAll('.admin-only');
            if (currentUser.role === 'ADMIN') {
                adminElements.forEach(el => el.style.display = '');
            } else {
                adminElements.forEach(el => el.style.display = 'none');
            }

            switchTab('cotizador');
            loadInventory();
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
                    showAppScreen();
                } else {
                    errBox.innerText = data.error || 'Credenciales incorrectas.';
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
            showLoginScreen();
        }

        function switchTab(tabId) {
            if (currentUser && currentUser.role !== 'ADMIN' && tabId !== 'cotizador') {
                tabId = 'cotizador';
            }

            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
            
            const target = document.getElementById('tab-' + tabId);
            if (target) target.classList.add('active');

            const btns = Array.from(document.querySelectorAll('.nav-btn'));
            const matched = btns.find(b => b.getAttribute('onclick') && b.getAttribute('onclick').includes(tabId));
            if (matched) matched.classList.add('active');

            if (tabId === 'inventario') loadInventory();
            if (tabId === 'caja') loadCashFlow();
            if (tabId === 'historial') loadInvoices();
            if (tabId === 'usuarios') loadUsers();
        }

        function reloadCurrentData() {
            loadInventory();
            if (currentUser && currentUser.role === 'ADMIN') {
                loadCashFlow();
                loadInvoices();
                loadUsers();
            }
        }

        // ==================== INVENTARIO & COTIZADOR ====================
        async function loadInventory() {
            try {
                const res = await fetch('/api/inventory', { headers: getAuthHeaders() });
                if (res.status === 401) { showLoginScreen(); return; }
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
                const row = document.createElement('div');
                row.className = 'bead-row';
                row.innerHTML = `
                    <div>
                        <div style="font-weight:600; font-size:0.95rem;">Balín Diamantado ${item.size}</div>
                        <div style="font-size:0.8rem; color:var(--text-muted);">
                            Venta: <strong style="color:var(--gold-primary);">${formatCOP(item.price_unit)}</strong>
                            ${item.stock !== undefined ? ` | Stock: <span style="color:${item.stock > 0 ? 'var(--success)' : 'var(--danger)'}; font-weight:700;">${item.stock} unids</span>` : ''}
                            ${item.stock === 0 ? '<span style="color:var(--danger); font-weight:700;"> (Agotado)</span>' : ''}
                        </div>
                    </div>
                    <div class="bead-counter">
                        <button type="button" onclick="changeQuoteQty('${item.size}', -1)">-</button>
                        <input type="number" id="input-${item.size.replace(' ', '')}" value="${quoteSelection[item.size]}" min="0" onchange="setQuoteQty('${item.size}', this.value)">
                        <button type="button" onclick="changeQuoteQty('${item.size}', 1)">+</button>
                    </div>
                `;
                container.appendChild(row);
            });
            calculateQuoteLive();
        }

        function changeQuoteQty(size, delta) {
            quoteSelection[size] = Math.max(0, (quoteSelection[size] || 0) + delta);
            document.getElementById('input-' + size.replace(' ', '')).value = quoteSelection[size];
            calculateQuoteLive();
        }

        function setQuoteQty(size, val) {
            quoteSelection[size] = Math.max(0, parseInt(val) || 0);
            calculateQuoteLive();
        }

        function resetQuote() {
            for (let s in quoteSelection) quoteSelection[s] = 0;
            renderQuoteBeadsList();
        }

        function calculateQuoteLive() {
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
                    surcharge = subtotalProfit * 0.15;
                    ruleLabel = "Mayor o igual a $500.000: Se suma 15% de confección de manilla.";
                    isHighValue = true;
                }
            } else {
                ruleLabel = "Selecciona balines para calcular la tarifa.";
            }

            const finalPrice = subtotalProfit + surcharge;
            const netProfit = finalPrice - costBase;

            currentQuoteResult = { totalBeadsCount, breakdown, costBase, subtotalProfit, surcharge, ruleLabel, isHighValue, finalPrice, netProfit };

            document.getElementById('quote-total-display').innerText = formatCOP(finalPrice);
            document.getElementById('quote-sub-display').innerText = `${totalBeadsCount} balines seleccionados`;
            const alertBox = document.getElementById('quote-rule-alert');
            alertBox.innerText = (isHighValue ? "⭐ " : "ℹ️ ") + ruleLabel;
            if (isHighValue) alertBox.classList.add('high-value'); else alertBox.classList.remove('high-value');

            document.getElementById('quote-cost-base').innerText = formatCOP(costBase);
            document.getElementById('quote-beads-subtotal').innerText = formatCOP(subtotalProfit);
            document.getElementById('quote-surcharge-val').innerText = formatCOP(surcharge);
            document.getElementById('quote-profit-val').innerText = formatCOP(netProfit);
        }

        function copyQuoteForWhatsApp() {
            if (!currentQuoteResult || currentQuoteResult.totalBeadsCount === 0) {
                alert("Selecciona al menos 1 balín para generar la cotización.");
                return;
            }

            let text = `✨ *COTIZACIÓN DE MANILLA EN ORO 18K* ✨\\n`;
            text += `Joyería Fina & Balines Diamantados\\n\\n`;
            text += `💎 *Detalle de balines seleccionados:*\\n`;
            currentQuoteResult.breakdown.forEach(b => {
                text += `• ${b.qty} balines de ${b.size}: ${formatCOP(b.price)}\\n`;
            });
            text += `\\n📌 *Subtotal balines:* ${formatCOP(currentQuoteResult.subtotalProfit)}\\n`;
            text += `🧵 *Confección y ensamble:* ${formatCOP(currentQuoteResult.surcharge)}\\n`;
            text += `💰 *TOTAL A PAGAR:* ${formatCOP(currentQuoteResult.finalPrice)} COP\\n\\n`;
            text += `Oro 18K garantizado de por vida. Tejido a la medida.`;

            navigator.clipboard.writeText(text).then(() => {
                alert("¡Cotización copiada al portapapeles! Ya puedes pegarla en WhatsApp.");
            }).catch(() => {
                prompt("Copia este texto para WhatsApp:", text);
            });
        }

        function transferQuoteToInvoice() {
            if (!currentQuoteResult || currentQuoteResult.totalBeadsCount === 0) {
                alert("Por favor selecciona al menos 1 balín.");
                return;
            }
            const summaryText = currentQuoteResult.breakdown.map(b => `${b.qty} de ${b.size}`).join(", ");
            pendingInvoiceItems = [{
                description: `Manilla con ${currentQuoteResult.totalBeadsCount} balines diamantados`,
                beads_details: JSON.stringify(quoteSelection),
                beads_summary: summaryText,
                cost_amount: currentQuoteResult.costBase,
                subtotal_beads: currentQuoteResult.subtotalProfit,
                surcharge: currentQuoteResult.surcharge,
                surcharge_rule: currentQuoteResult.isHighValue ? "+15% Manilla" : "+$50.000 Fijo",
                total_price: currentQuoteResult.finalPrice,
                profit_amount: currentQuoteResult.netProfit
            }];
            renderInvoiceItems();
            switchTab('facturacion');
        }

        function renderInvoiceItems() {
            const container = document.getElementById('invoice-items-table-container');
            const summary = document.getElementById('invoice-items-summary');
            const tbody = document.getElementById('invoice-items-tbody');

            if (pendingInvoiceItems.length === 0) {
                container.style.display = 'none';
                summary.style.display = 'block';
                document.getElementById('inv-summary-beads').innerText = '$ 0';
                document.getElementById('inv-summary-surcharge').innerText = '$ 0';
                document.getElementById('inv-summary-total').innerText = '$ 0';
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
                <td>${item.surcharge_rule} (${formatCOP(item.surcharge)})</td>
                <td><strong>${formatCOP(item.total_price)}</strong></td>
                <td><button class="btn btn-danger btn-sm" onclick="clearInvoiceForm()">✕</button></td>
            `;
            tbody.appendChild(tr);

            document.getElementById('inv-summary-beads').innerText = formatCOP(item.subtotal_beads);
            document.getElementById('inv-summary-surcharge').innerText = formatCOP(item.surcharge);
            document.getElementById('inv-summary-total').innerText = formatCOP(item.total_price);
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
            if (pendingInvoiceItems.length === 0) return alert("Carga una manilla antes de facturar.");

            const item = pendingInvoiceItems[0];
            const payload = {
                customer_name: customerName,
                customer_phone: document.getElementById('inv-customer-phone').value.trim(),
                customer_id_doc: document.getElementById('inv-customer-id').value.trim(),
                notes: document.getElementById('inv-notes').value.trim(),
                subtotal_beads: item.subtotal_beads,
                bracelet_surcharge: item.surcharge,
                surcharge_rule: item.surcharge_rule,
                total_amount: item.total_price,
                cost_amount: item.cost_amount,
                profit_amount: item.profit_amount,
                beads_details: item.beads_details,
                item_description: item.description
            };

            const res = await fetch('/api/invoices', {
                method: 'POST',
                headers: getAuthHeaders(),
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            alert(`¡Factura ${data.invoice_number} creada exitosamente!\nSe descontaron los balines del inventario y se registró el ingreso en caja.`);
            clearInvoiceForm();
            loadInventory();
            openInvoiceModal(data.invoice_id);
        }

        function renderInventoryTable() {
            const tbody = document.getElementById('inventory-tbody');
            if (!tbody) return;
            tbody.innerHTML = '';
            inventoryData.forEach(item => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${item.size}</strong></td>
                    <td>
                        <div style="display:flex; align-items:center; gap:6px;">
                            <input type="number" id="inline-stock-${item.id}" value="${item.stock || 0}" min="0" 
                                   class="form-control" style="width:75px; padding:4px 8px; font-weight:700; text-align:center; color:#fff;"
                                   onkeydown="if(event.key==='Enter') saveInlineStock(${item.id})">
                            <button class="btn btn-gold btn-sm" style="padding:5px 9px;" title="Guardar este stock" onclick="saveInlineStock(${item.id})">💾</button>
                        </div>
                        ${item.stock === 0 ? '<span style="color:var(--danger); font-size:0.75rem; font-weight:700;">(Agotado)</span>' : ''}
                    </td>
                    <td>${formatCOP(item.cost)}</td>
                    <td><strong style="color:var(--gold-light);">${item.gain_unit_pct}%</strong></td>
                    <td style="color:var(--gold-primary); font-weight:600;">${formatCOP(item.price_unit)}</td>
                    <td><strong style="color:var(--text-muted);">${item.gain_x20_pct}%</strong></td>
                    <td>${formatCOP(item.price_unit_x20)}</td>
                    <td>${formatCOP(item.price_total_x20)}</td>
                    <td>
                        <div style="display:flex; gap:5px;">
                            <button class="btn btn-gold btn-sm" onclick="openEditBeadModal(${item.id})" title="Editar porcentaje de ganancia y costo">✏️ % Ganancia</button>
                            <button class="btn btn-secondary btn-sm" onclick="quickRestockPrompt('${item.size}')" title="Registrar compra">+ Compra</button>
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
                alert("Ingresa un número válido.");
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
                    await loadInventory();
                    alert("✅ Stock guardado correctamente.");
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
                    closeEditBeadModal();
                    await loadInventory();
                    alert("✅ Porcentajes de ganancia y precios actualizados exitosamente.");
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
                    closeBulkStockModal();
                    await loadInventory();
                    alert("✅ Todo el inventario físico fue actualizado exitosamente.");
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
            closeRestockModal();
            loadInventory();
        }

        // ==================== CAJA & GASTOS ====================
        async function loadCashFlow() {
            try {
                const res = await fetch('/api/cashflow', { headers: getAuthHeaders() });
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
            document.getElementById('cf-desc').value = '';
            document.getElementById('cf-amount').value = '';
            loadCashFlow();
        }

        async function deleteCashFlow(id) {
            if (!confirm("¿Eliminar este registro de caja?")) return;
            await fetch('/api/cashflow/' + id, { method: 'DELETE', headers: getAuthHeaders() });
            loadCashFlow();
        }

        // ==================== FACTURAS ====================
        async function loadInvoices() {
            try {
                const res = await fetch('/api/invoices', { headers: getAuthHeaders() });
                const list = await res.json();
                const tbody = document.getElementById('invoices-tbody');
                tbody.innerHTML = '';
                list.forEach(inv => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong style="color:var(--gold-primary);">${inv.invoice_number}</strong></td>
                        <td>${inv.date}</td>
                        <td>${inv.customer_name}</td>
                        <td>${inv.customer_phone || '-'}</td>
                        <td><strong>${formatCOP(inv.total_amount)}</strong></td>
                        <td style="color:var(--success);">${formatCOP(inv.profit_amount)}</td>
                        <td><button class="btn btn-secondary btn-sm" onclick="openInvoiceModal(${inv.id})">🖨️ Ver Factura</button></td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (err) {}
        }

        async function openInvoiceModal(invoiceId) {
            const res = await fetch('/api/invoices/' + invoiceId, { headers: getAuthHeaders() });
            const inv = await res.json();
            document.getElementById('modal-inv-number').innerText = `FACTURA N° ${inv.invoice_number}`;
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
                    <td><strong>Manilla con Balines Diamantados Oro 18K</strong></td>
                    <td>${beadListText.join(', ')}</td>
                    <td style="text-align:right; font-weight:600;">${formatCOP(inv.subtotal_beads)}</td>
                </tr>
            `;

            document.getElementById('modal-inv-subtotal').innerText = formatCOP(inv.subtotal_beads);
            document.getElementById('modal-inv-rule-label').innerText = `Confección (${inv.surcharge_rule}):`;
            document.getElementById('modal-inv-surcharge').innerText = formatCOP(inv.bracelet_surcharge);
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

        // ==================== GESTIÓN DE USUARIOS ====================
        async function loadUsers() {
            try {
                const res = await fetch('/api/users', { headers: getAuthHeaders() });
                if (!res.ok) return;
                const users = await res.json();
                const tbody = document.getElementById('users-tbody');
                tbody.innerHTML = '';
                users.forEach(u => {
                    const isAdmin = u.role === 'ADMIN';
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td><strong>${u.name}</strong></td>
                        <td>${u.email}</td>
                        <td><span class="badge-role ${isAdmin ? 'badge-admin' : 'badge-cotizador'}">${u.role}</span></td>
                        <td>
                            ${u.email === currentUser.email ? '<span style="color:var(--text-muted); font-size:0.8rem;">(Tú)</span>' : `<button class="btn btn-danger btn-sm" onclick="deleteUser(${u.id})">Eliminar</button>`}
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
                    alert("✅ Usuario creado exitosamente.");
                    document.getElementById('new-user-name').value = '';
                    document.getElementById('new-user-email').value = '';
                    document.getElementById('new-user-password').value = '';
                    loadUsers();
                } else {
                    alert("Error: " + (data.error || "No se pudo crear el usuario."));
                }
            } catch (err) {
                alert("Error de conexión al crear usuario.");
            }
        }

        async function deleteUser(userId) {
            if (!confirm("¿Seguro que deseas eliminar este usuario?")) return;
            try {
                const res = await fetch('/api/users/' + userId, { method: 'DELETE', headers: getAuthHeaders() });
                const data = await res.json();
                if (data.success) {
                    loadUsers();
                } else {
                    alert("Error al eliminar usuario.");
                }
            } catch (err) {
                alert("Error al eliminar usuario.");
            }
        }

        // Inicializar fecha
        document.getElementById('cf-date').value = new Date().toISOString().split('T')[0];

        // Verificar sesión activa al cargar
        checkAuth();
    </script>
</body>
</html>
"""

# Servidor HTTP con control de acceso por tokens y roles
class JewelryRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        # Rutas protegidas que requieren autenticación
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

        elif path == "/api/inventory":
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM beads_inventory ORDER BY id ASC")
            rows = [dict(r) for r in c.fetchall()]
            conn.close()

            # SI ES COTIZADOR: Entregar costo para calcular ganancia, pero ocultar porcentajes de configuración
            if user["role"] == "COTIZADOR":
                filtered_rows = []
                for r in rows:
                    filtered_rows.append({
                        "id": r["id"],
                        "size": r["size"],
                        "cost": r["cost"],
                        "price_unit": r["price_unit"],
                        "stock": r["stock"]
                    })
                self._send_json(filtered_rows)
                return

            # Si es ADMIN, entrega todos los datos de costos y márgenes completos
            self._send_json(rows)
            return

        # Rutas exclusivas de ADMIN
        if user["role"] != "ADMIN":
            self._send_json({"error": "Acceso denegado: se requieren permisos de Administrador"}, status=403)
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

        else:
            self.send_error(404, "No encontrada")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

        # 1. Login no requiere token previo
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
                          (token, user["id"], user["role"], now_str))
                conn.commit()
                conn.close()
                self._send_json({
                    "success": True,
                    "token": token,
                    "user": {
                        "id": user["id"],
                        "email": user["email"],
                        "name": user["name"],
                        "role": user["role"]
                    }
                })
                return
            else:
                conn.close()
                self._send_json({"success": False, "error": "Correo o contraseña incorrectos."}, status=401)
                return

        # 2. Rutas que requieren sesión activa
        user = authenticate_request(self.headers)
        if not user:
            self._send_json({"error": "No autenticado"}, status=401)
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

        # 3. Rutas exclusivas de ADMIN
        if user["role"] != "ADMIN":
            self._send_json({"error": "Acceso denegado: solo administradores"}, status=403)
            return

        if path == "/api/invoices":
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as count FROM invoices")
            count = c.fetchone()["count"] + 1
            invoice_num = f"FACT-{count:04d}"
            today_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            beads_details = json.loads(payload.get("beads_details", "{}"))
            for size, qty in beads_details.items():
                if qty > 0:
                    c.execute("UPDATE beads_inventory SET stock = stock - ? WHERE size = ?", (qty, size))

            c.execute("""
                INSERT INTO invoices (
                    invoice_number, date, customer_name, customer_phone, customer_id_doc,
                    subtotal_beads, bracelet_surcharge, surcharge_rule, total_amount,
                    cost_amount, profit_amount, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                invoice_num, today_str, payload.get("customer_name"),
                payload.get("customer_phone", ""), payload.get("customer_id_doc", ""),
                payload.get("subtotal_beads", 0), payload.get("bracelet_surcharge", 0),
                payload.get("surcharge_rule", ""), payload.get("total_amount", 0),
                payload.get("cost_amount", 0), payload.get("profit_amount", 0),
                payload.get("notes", "")
            ))
            invoice_id = c.lastrowid

            c.execute("""
                INSERT INTO cash_flow (date, type, category, description, amount, invoice_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().strftime("%Y-%m-%d"), "INGRESO", "Venta Manilla",
                f"Venta Factura {invoice_num} - {payload.get('customer_name')}",
                payload.get("total_amount", 0), invoice_id
            ))

            conn.commit()
            conn.close()
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
            conn.commit()
            conn.close()
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
            conn.commit()
            conn.close()
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
                        f"Compra {qty} balines {size}",
                        unit_cost * qty
                    ))

            conn.commit()
            conn.close()
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
            conn.commit()
            conn.close()
            self._send_json({"success": True})
            return

        elif path == "/api/users":
            name = payload.get("name", "").strip()
            email = payload.get("email", "").strip().lower()
            password = payload.get("password", "")
            role = payload.get("role", "COTIZADOR")

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
                conn.commit()
                conn.close()
                self._send_json({"success": True})
                return
            except sqlite3.IntegrityError:
                conn.close()
                self._send_json({"error": "Este correo ya se encuentra registrado."}, status=400)
                return

        else:
            self.send_error(404, "No encontrada")

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        
        user = authenticate_request(self.headers)
        if not user or user["role"] != "ADMIN":
            self._send_json({"error": "Acceso denegado"}, status=403)
            return

        if path.startswith("/api/cashflow/"):
            flow_id = path.split("/")[-1]
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM cash_flow WHERE id = ?", (flow_id,))
            conn.commit()
            conn.close()
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
            conn.commit()
            conn.close()
            self._send_json({"success": True})
            return

        self.send_error(404, "No encontrada")

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

def start_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), JewelryRequestHandler) as httpd:
        url = f"http://localhost:{PORT}"
        print("=" * 65)
        print("  ✨ SISTEMA DE JOYERÍA: MANILLAS CON BALINES DE ORO ✨")
        print("  SISTEMA MULTI-USUARIO CON ROLES ADMIN Y COTIZADOR")
        print("=" * 65)
        print(f"  Servidor iniciado en: {url}")
        print("  Abre la URL en tu navegador web o en tu celular.")
        print("  Presiona Ctrl + C en la terminal para detener la aplicación.")
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