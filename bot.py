import requests
from bs4 import BeautifulSoup
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime
import sqlite3
import re
from abc import ABC, abstractmethod

st.set_page_config(page_title="JCR Cards Bot Pro", page_icon="🎴", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
h1 { background: linear-gradient(90deg, #667eea, #764ba2, #f093fb); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800 !important; }
.stButton>button { border-radius: 12px !important; padding: 10px 20px !important; font-weight: 600 !important; border: none !important; background: linear-gradient(90deg, #667eea, #764ba2) !important; color: white !important; transition: all 0.3s ease !important; }
.stButton>button:hover { transform: scale(1.03); box-shadow: 0 4px 20px rgba(102,126,234,0.4) !important; }
.url-card { background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 10px; padding: 12px; margin: 8px 0; }
.url-active { border-left: 4px solid #38ef7d; }
.url-inactive { border-left: 4px solid #ef473a; opacity: 0.6; }
.status-active { background: linear-gradient(90deg, #11998e, #38ef7d); padding: 4px 12px; border-radius: 20px; color: white; font-weight: bold; font-size: 13px; display: inline-block; }
.status-inactive { background: linear-gradient(90deg, #cb2d3e, #ef473a); padding: 4px 12px; border-radius: 20px; color: white; font-weight: bold; font-size: 13px; display: inline-block; }
[data-testid="stMetric"] { background: rgba(255, 255, 255, 0.05); border-radius: 12px; padding: 14px; border: 1px solid rgba(255, 255, 255, 0.1); }
[data-testid="stMetricLabel"] { color: #a0a0a0 !important; }
[data-testid="stMetricValue"] { color: #ffffff !important; font-weight: 700 !important; }
</style>
""", unsafe_allow_html=True)

def init_db():
    conn = sqlite3.connect("jcr_bot.db", check_same_thread=False)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS historial (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, url TEXT, tienda TEXT, estado TEXT, precio TEXT, precio_anterior TEXT, cambio_precio TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS precios_actuales (url TEXT PRIMARY KEY, precio TEXT, timestamp TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS urls_vigiladas (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, nombre TEXT, activa INTEGER DEFAULT 1, fecha_agregada TEXT)")
    conn.commit()
    return conn

def agregar_url(conn, url, nombre=None):
    try:
        c = conn.cursor()
        if not nombre:
            nombre = url.split("/")[2] if len(url.split("/")) > 2 else url
        c.execute("INSERT INTO urls_vigiladas (url, nombre, activa, fecha_agregada) VALUES (?, ?, 1, ?)", (url, nombre, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def eliminar_url(conn, url_id):
    c = conn.cursor()
    c.execute("DELETE FROM urls_vigiladas WHERE id = ?", (url_id,))
    conn.commit()

def toggle_url(conn, url_id, estado_actual):
    c = conn.cursor()
    c.execute("UPDATE urls_vigiladas SET activa = ? WHERE id = ?", (0 if estado_actual else 1, url_id))
    conn.commit()

def obtener_urls(conn, solo_activas=True):
    query = "SELECT id, url, nombre, activa FROM urls_vigiladas"
    if solo_activas:
        query += " WHERE activa = 1"
    query += " ORDER BY fecha_agregada DESC"
    c = conn.cursor()
    c.execute(query)
    rows = c.fetchall()
    return [{'id': r[0], 'url': r[1], 'nombre': r[2], 'activa': bool(r[3])} for r in rows]

def guardar_log(conn, url, tienda, estado, precio, precio_anterior="N/A", cambio="N/A"):
    c = conn.cursor()
    c.execute("INSERT INTO historial (timestamp, url, tienda, estado, precio, precio_anterior, cambio_precio) VALUES (?, ?, ?, ?, ?, ?, ?)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), url, tienda, estado, precio, precio_anterior, cambio))
    conn.commit()

def actualizar_precio_actual(conn, url, precio):
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO precios_actuales (url, precio, timestamp) VALUES (?, ?, ?)", (url, precio, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()

def obtener_precio_anterior(conn, url):
    c = conn.cursor()
    c.execute("SELECT precio FROM precios_actuales WHERE url = ?", (url,))
    row = c.fetchone()
    return row[0] if row else "N/A"

def obtener_historial(conn, limite=100):
    c = conn.cursor()
    c.execute(f"SELECT * FROM historial ORDER BY id DESC LIMIT {limite}")
    rows = c.fetchall()
    return [{'timestamp': r[1], 'url': r[2], 'tienda': r[3], 'estado': r[4], 'precio': r[5], 'precio_anterior': r[6], 'cambio_precio': r[7]} for r in rows]

def parse_precio_num(precio_str):
    if precio_str == "N/A" or not precio_str:
        return None
    nums = re.findall(r'[\d]+[.,]?\d*', str(precio_str).replace(',', '.'))
    if nums:
        try:
            return float(nums[0])
        except:
            return None
    return None
        
class BaseParser(ABC):
    @abstractmethod
    def parse(self, soup, html):
        pass
    def get_name(self, url):
        return url.split("/")[2] if len(url.split("/")) > 2 else url
    def hay_bloqueo(self, texto):
        palabras_bloqueo = ["venta bloqueada", "agotado", "out of stock", "sold out", "no disponible", "notify me", "avisame cuando", "sin stock", "producto no disponible", "temporalmente agotado"]
        return any(pal in texto for pal in palabras_bloqueo)
    def hay_boton_anadir_real(self, soup):
        botones = soup.find_all(['button', 'a', 'input', 'form'], class_=lambda x: x and any(kw in str(x).lower() for kw in ['cart', 'add', 'comprar', 'buy', 'submit']))
        boton_id = soup.find(id=lambda x: x and any(kw in str(x).lower() for kw in ['add-to-cart', 'buy-now', 'comprar']))
        if boton_id:
            botones.append(boton_id)
        for boton in botones:
            texto_boton = boton.get_text(strip=True).lower()
            if any(pal in texto_boton for pal in ['añadir', 'comprar', 'add to cart', 'buy', 'agregar']):
                if not boton.get('disabled') and 'disabled' not in str(boton.get('class', [])):
                    return True
        return False

class TCGFactoryParser(BaseParser):
    def parse(self, soup, html):
        texto_completo = soup.get_text(separator=" ", strip=True).lower()
        if self.hay_bloqueo(texto_completo):
            return False, self._extraer_precio(html, soup)
        if self.hay_boton_anadir_real(soup):
            return True, self._extraer_precio(html, soup)
        palabras_compra = ["añadir al carrito", "add to cart", "comprar ahora", "buy now"]
        if any(pal in texto_completo for pal in palabras_compra):
            return True, self._extraer_precio(html, soup)
        return False, self._extraer_precio(html, soup)
    def _extraer_precio(self, html, soup):
        patrones = [r'(\d+[.,]\d{2}\s*[€$£])', r'([€$£]\s*\d+[.,]\d{2})', r'"price":"?(\d+[.,]\d{2})"?']
        for p in patrones:
            match = re.search(p, html, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return "N/A"

class AmazonParser(BaseParser):
    def parse(self, soup, html):
        unavailable = soup.find(id='outOfStock') or soup.find(id='availability')
        if unavailable and any(pal in unavailable.get_text().lower() for pal in ['no disponible', 'agotado', 'out of stock']):
            return False, "N/A"
        buy_box = soup.find(id='buy-now-button') or soup.find(id='add-to-cart-button')
        precio = "N/A"
        price_whole = soup.find('span', class_='a-price-whole')
        if price_whole:
            precio = price_whole.get_text(strip=True).replace('.', ',') + '€'
        disponible = buy_box is not None
        return disponible, precio

class GenericParser(BaseParser):
    def parse(self, soup, html):
        texto_completo = soup.get_text(separator=" ", strip=True).lower()
        if self.hay_bloqueo(texto_completo):
            precio = self._extraer_precio(html)
            return False, precio
        if self.hay_boton_anadir_real(soup):
            precio = self._extraer_precio(html)
            return True, precio
        palabras_compra = ["añadir al carrito", "add to cart", "comprar", "buy now", "pre-order", "preventa"]
        if any(pal in texto_completo for pal in palabras_compra):
            precio = self._extraer_precio(html)
            return True, precio
        precio = self._extraer_precio(html)
        return False, precio
    def _extraer_precio(self, html):
        patrones = [r'(\d+[.,]\d{2}\s*[€$£])', r'([€$£]\s*\d+[.,]\d{2})', r'"price":"?(\d+[.,]\d{2})"?']
        for p in patrones:
            match = re.search(p, html, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return "N/A"

class ParserFactory:
    @staticmethod
    def get_parser(url):
        domain = url.lower()
        if 'tcgfactory' in domain:
            return TCGFactoryParser()
        if 'amazon' in domain:
            return AmazonParser()
        return GenericParser()

class Notifier:
    @staticmethod
    def telegram(mensaje, token, chat_id):
        if not token or not chat_id:
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            r = requests.post(url, json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=10)
            return r.status_code == 200
        except:
            return False

def enviar_notificacion(mensaje, config):
    if config.get('telegram_enabled') and config.get('telegram_token') and config.get('telegram_chat_id'):
        Notifier.telegram(mensaje, config['telegram_token'], config['telegram_chat_id'])

if "conn" not in st.session_state:
    st.session_state.conn = init_db()
if "corriendo" not in st.session_state:
    st.session_state.corriendo = False
if "ultima_comprobacion" not in st.session_state:
    st.session_state.ultima_comprobacion = None

def verificar_url(url, config):
    parser = ParserFactory.get_parser(url)
    tienda = parser.__class__.__name__.replace('Parser', '').lower()
    nombre = parser.get_name(url)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36", "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            disponible, precio = parser.parse(soup, r.text)
            precio_anterior = obtener_precio_anterior(st.session_state.conn, url)
            cambio_precio = "Sin cambio"
            precio_actual_num = parse_precio_num(precio)
            precio_anterior_num = parse_precio_num(precio_anterior)
            if precio_actual_num and precio_anterior_num and precio_anterior != "N/A":
                diferencia_pct = ((precio_anterior_num - precio_actual_num) / precio_anterior_num) * 100
                umbral = config.get('price_drop_threshold', 5)
                if diferencia_pct >= umbral:
                    cambio_precio = f"⬇️ -{diferencia_pct:.1f}%"
                    msg = f"🔥 *¡BAJADA DE PRECIO!* 🔥\n\n🏪 {tienda.upper()}\n📦 {nombre}\n💸 Antes: {precio_anterior}\n💰 Ahora: {precio}\n📉 Descuento: {diferencia_pct:.1f}%\n🔗 {url}"
                    enviar_notificacion(msg, config)
                elif diferencia_pct <= -umbral:
                    cambio_precio = f"⬆️ +{abs(diferencia_pct):.1f}%"
                else:
                    cambio_precio = "↔️ Estable"
            estado = "DISPONIBLE" if disponible else "AGOTADO"
            if disponible:
                msg = f"🚨 *¡STOCK!* 🚨\n🏪 {tienda.upper()}\n📦 {nombre}\n💰 {precio}\n🔗 {url}"
                enviar_notificacion(msg, config)
            guardar_log(st.session_state.conn, url, tienda, estado, precio, precio_anterior, cambio_precio)
            if precio != "N/A":
                actualizar_precio_actual(st.session_state.conn, url, precio)
            return {'url': url, 'tienda': tienda, 'estado': estado, 'precio': precio, 'ok': True}
        else:
            guardar_log(st.session_state.conn, url, 'error', f'HTTP_{r.status_code}', 'N/A')
            return {'url': url, 'tienda': 'error', 'estado': 'ERROR', 'ok': False}
    except Exception as e:
        guardar_log(st.session_state.conn, url, 'error', 'EXCEPTION', 'N/A')
        return {'url': url, 'tienda': 'error', 'estado': 'ERROR', 'ok': False}

def verificar_todas(lista_urls, config):
    for url in lista_urls:
        verificar_url(url, config)
    st.session_state.ultima_comprobacion = datetime.now().strftime("%H:%M:%S")
        
st.title("🎴 JCR Cards Bot Pro")
st.markdown("##### Gestor de URLs · Multi-tienda · Alertas de precio")

with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    intervalo = st.slider("⏱️ Intervalo (min)", 1, 60, 3)
    price_threshold = st.slider("📉 Umbral alerta precio (%)", 1, 50, 5)
    st.markdown("---")
    st.markdown("### 🔗 Gestión de URLs")
    with st.form("add_url_form"):
        nueva_url = st.text_input("🌐 URL del producto", placeholder="https://tienda.com/producto...")
        nombre_personalizado = st.text_input("📝 Nombre (opcional)", placeholder="Ej: Booster OP-17")
        if st.form_submit_button("➕ Añadir URL", use_container_width=True):
            if nueva_url.strip():
                if not nueva_url.startswith(('http://', 'https://')):
                    nueva_url = 'https://' + nueva_url
                if agregar_url(st.session_state.conn, nueva_url.strip(), nombre_personalizado.strip() or None):
                    st.success("✅ URL añadida correctamente")
                    st.rerun()
                else:
                    st.warning("⚠️ Esta URL ya está en la lista")
            else:
                st.error("⚠️ Introduce una URL válida")
    urls_lista = obtener_urls(st.session_state.conn, solo_activas=False)
    if urls_lista:
        st.markdown(f"**📡 {len(urls_lista)} URLs registradas**")
        for row in urls_lista:
            estado_icon = "🟢" if row['activa'] else "🔴"
            clase = "url-active" if row['activa'] else "url-inactive"
            st.markdown(f'<div class="url-card {clase}"><div><strong>{estado_icon} {row["nombre"]}</strong><br><small>{row["url"][:40]}...</small></div></div>', unsafe_allow_html=True)
            cols = st.columns(3)
            with cols[0]:
                if st.button("🗑️", key=f"del_{row['id']}", help="Eliminar"):
                    eliminar_url(st.session_state.conn, row['id'])
                    st.rerun()
            with cols[1]:
                if st.button("⏸️" if row['activa'] else "▶️", key=f"toggle_{row['id']}", help="Pausar/Activar"):
                    toggle_url(st.session_state.conn, row['id'], row['activa'])
                    st.rerun()
            with cols[2]:
                st.write("")
    else:
        st.info("🕊️ No tienes URLs añadidas todavía")
    st.markdown("---")
    st.markdown("### 📨 Telegram")
    tg_enabled = st.checkbox("Activar notificaciones", value=True)
    tg_token = st.text_input("🔑 Bot Token", type="password")
    tg_chat_id = st.text_input("🆔 Chat ID")
    config = {'telegram_enabled': tg_enabled, 'telegram_token': tg_token, 'telegram_chat_id': tg_chat_id, 'price_drop_threshold': price_threshold}
    if st.button("🗑️ Limpiar historial"):
        c = st.session_state.conn.cursor()
        c.execute("DELETE FROM historial")
        c.execute("DELETE FROM precios_actuales")
        st.session_state.conn.commit()
        st.success("✅ Historial borrado")
        st.rerun()

urls_activas = obtener_urls(st.session_state.conn, solo_activas=True)
historial = obtener_historial(st.session_state.conn)
mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("📡 URLs activas", len(urls_activas))
disponibles = len([h for h in historial if h['estado'] == 'DISPONIBLE'])
agotados = len([h for h in historial if h['estado'] == 'AGOTADO'])
mc2.metric("✅ Disponibles", disponibles)
mc3.metric("🚫 Agotados", agotados)
badge = '<span class="status-active">● ACTIVO</span>' if st.session_state.corriendo else '<span class="status-inactive">● DETENIDO</span>'
mc4.markdown(f"**Estado**<br>{badge}", unsafe_allow_html=True)
st.markdown("---")
ca, cb, cc = st.columns(3)
with ca:
    if st.button("🚀 Iniciar Monitor 24/7", use_container_width=True):
        if urls_activas:
            st.session_state.corriendo = True
            st.rerun()
        else:
            st.error("⚠️ No hay URLs activas. Añade URLs en el panel lateral.")
with cb:
    if st.button("🔍 Comprobar Ahora", use_container_width=True):
        if urls_activas:
            with st.status("🔍 Analizando tiendas...", expanded=True) as s:
                urls_lista_check = [u['url'] for u in urls_activas]
                for url in urls_lista_check:
                    res = verificar_url(url, config)
                    icon = "✅" if res.get('ok') else "❌"
                    st.write(f"{icon} {res['tienda'].upper()}: {res['estado']} ({res.get('precio', 'N/A')})")
                s.update(label="✅ Análisis completado", state="complete")
                st.session_state.ultima_comprobacion = datetime.now().strftime("%H:%M:%S")
                st.rerun()
        else:
            st.error("⚠️ No hay URLs activas.")
with cc:
    if st.button("⏹️ Detener Monitor", use_container_width=True):
        st.session_state.corriendo = False
        st.rerun()

if st.session_state.corriendo:
    st_autorefresh(interval=int(intervalo * 60 * 1000), key="auto_v41")
    if urls_activas:
        urls_lista_check = [u['url'] for u in urls_activas]
        verificar_todas(urls_lista_check, config)

st.markdown("---")
tab1, tab2 = st.tabs(["📊 Estado Actual", "📜 Historial"])
with tab1:
    st.subheader("📊 Estado de tus URLs")
    if historial:
        seen_urls = set()
        for row in historial:
            if row['url'] not in seen_urls:
                seen_urls.add(row['url'])
                icon = "🟢" if row['estado'] == 'DISPONIBLE' else "🔴"
                st.markdown(f'<div class="url-card"><div><h4>{icon} {row["url"]}</h4><p><strong>Tienda:</strong> {row["tienda"].upper()} | <strong>Estado:</strong> {row["estado"]} | <strong>Precio:</strong> {row["precio"]}</p><small>Última comprobación: {row["timestamp"]}</small></div></div>', unsafe_allow_html=True)
    else:
        st.info("🕊️ Haz clic en 'Comprobar Ahora' para ver el estado actual.")
with tab2:
    st.subheader("📜 Historial completo")
    if historial:
        headers = ["Fecha", "Tienda", "Estado", "Precio", "Cambio"]
        st.markdown("| " + " | ".join(headers) + " |")
        st.markdown("|" + "---|" * len(headers))
        for row in historial[:50]:
            st.markdown(f"| {row['timestamp']} | {row['tienda']} | {row['estado']} | {row['precio']} | {row['cambio_precio']} |")
    else:
        st.info("🕊️ Sin registros todavía.")

st.caption(f"🎴 JCR Cards Bot Pro · Última comprobación: {st.session_state.ultima_comprobacion or 'Nunca'}")
