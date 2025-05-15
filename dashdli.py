# app.py
# ───────────────────────────────────────────────────────────────────────────────
# Dashboard Streamlit para o CSV privado do WordPress
# * Download automático com login (WP_USER / WP_PASS)
# * Cache inválido quando o arquivo muda
# * Filtros de Estado e Categoria, gráficos e tabelas
# * Proteção de acesso via código (ACCESS_CODE, múltiplos separados por vírgula)
# * Atualização automática a cada 10 min e botão de refresh manual
#
# Requisitos:
#   pip install streamlit pandas requests python-dotenv beautifulsoup4
#
# Execução:
#   streamlit run app.py
#
# Estrutura esperada:
#   ├─ .env   → WP_USER, WP_PASS, ACCESS_CODE
#   ├─ app.py
#   └─ docs/  (criada automaticamente)
# ───────────────────────────────────────────────────────────────────────────────

import os
import html
import pathlib
import datetime as dt
import requests
import tempfile
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import pandas as pd
import streamlit as st

# ══════════════════════════════════════════════════════════════════════════════
# 1. Carrega variáveis de ambiente .env
# ══════════════════════════════════════════════════════════════════════════════
load_dotenv()

# ══════════════════════════════════════════════════════════════════════════════
# 2. Bloqueio de Acesso por Código — tela centralizada
# ══════════════════════════════════════════════════════════════════════════════
VALID_CODES = {c.strip() for c in os.getenv("ACCESS_CODE", "").split(",") if c.strip()}

def rerun():                 # compat com versões antigas do Streamlit
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()

st.set_page_config(page_title="Dashboard (Acesso Restrito)", layout="wide")

if not VALID_CODES:
    st.error("ACCESS_CODE não definido em .env")
    st.stop()

if "auth_ok" not in st.session_state:
    st.session_state.auth_ok = False

# ─── Tela de login (aparece só enquanto não estiver autenticado) ──────────────
if not st.session_state.auth_ok:
    # esconde barra lateral
    st.markdown("""
        <style>[data-testid="stSidebar"] {display:none;}</style>
        """, unsafe_allow_html=True)

    st.markdown("<h2 style='text-align:center'>🔒 Área restrita</h2>",
                unsafe_allow_html=True)

    col_esq, col_meio, col_dir = st.columns([2, 3, 2])
    with col_meio:
        with st.form("login_form"):
            pwd = st.text_input("Código de acesso", type="password")
            entrar = st.form_submit_button("Entrar")

        if entrar:
            if pwd in VALID_CODES:
                st.session_state.auth_ok = True
                rerun()
            else:
                st.error("Código inválido")
    st.stop()

# (sidebar volta a aparecer após o login)
if st.sidebar.button("Sair"):
    st.session_state.auth_ok = False
    rerun()

# ══════════════════════════════════════════════════════════════════════════════
# 3. Parâmetros fixos e credenciais WP
# ══════════════════════════════════════════════════════════════════════════════
CSV_PATH   = pathlib.Path(__file__).parent / "docs" / "job_listings_export.csv"
LOGIN_URL  = "https://dialivredeimpostos.org.br/wp-login.php"
EXPORT_URL = "https://dialivredeimpostos.org.br/?export_jobs_csv"
MAX_AGE    = dt.timedelta(hours=1)         # baixa do WP se arquivo for mais velho

WP_USER = os.getenv("WP_USER")
WP_PASS = os.getenv("WP_PASS")
if not WP_USER or not WP_PASS:
    st.error("WP_USER / WP_PASS não definidos em .env")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# 4. Funções de download & update
# ══════════════════════════════════════════════════════════════════════════════
def download_csv() -> bytes:
    with requests.Session() as sess:
        # pega cookies
        sess.get(LOGIN_URL, timeout=10)
        payload = dict(
            log=WP_USER, pwd=WP_PASS, wp_submit="Log In",
            redirect_to=EXPORT_URL, testcookie="1",
        )
        sess.post(LOGIN_URL, data=payload, timeout=10, allow_redirects=True)
        r = sess.get(EXPORT_URL, timeout=30)
        r.raise_for_status()
        if "text/csv" not in r.headers.get("Content-Type", ""):
            raise ValueError("Resposta não é CSV. Verifique credenciais ou URL.")
        return r.content

def update_csv():
    """Baixa o CSV se não existir, se estiver ‘velho’ ou se o usuário pedir."""
    try:
        outdated = (
            not CSV_PATH.exists() or
            dt.datetime.now() - dt.datetime.fromtimestamp(CSV_PATH.stat().st_mtime) > MAX_AGE
        )
        if not outdated:
            return

        data = download_csv()
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", delete=False,
                                         dir=CSV_PATH.parent) as tmp:
            tmp.write(data)
            tmp.flush(); os.fsync(tmp.fileno())
            tmp_path = pathlib.Path(tmp.name)
        tmp_path.replace(CSV_PATH)
    except Exception as e:
        st.warning(f"⚠️  Não foi possível atualizar o CSV: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 5. Atualização automática e botão manual
# ══════════════════════════════════════════════════════════════════════════════
# força recarga se usuário clicar no botão
if st.sidebar.button("🔄 Atualizar dados agora"):
    update_csv()
    rerun()                               # recarrega tela imediatamente

# JavaScript: recarrega página a cada 10 min (600 000 ms)
st.markdown("""
    <script>
        const timeout = 600000;  // 10 minutos
        setTimeout(() => {window.location.reload();}, timeout);
    </script>
    """, unsafe_allow_html=True)

# antes de cada run, tenta baixar se necessário
update_csv()

# ══════════════════════════════════════════════════════════════════════════════
# 6. Carrega dados (cache invalida por mtime ou em 10 min)
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner=False, ttl=600)             # 10 min
def load_data(path: pathlib.Path, mtime: float) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).fillna("")
    def clean(val):
        if isinstance(val, str):
            return html.unescape(val).replace("–", "-").replace("—", "-")
        return val
    return df.applymap(clean)

mtime = CSV_PATH.stat().st_mtime
df = load_data(CSV_PATH, mtime)

# ══════════════════════════════════════════════════════════════════════════════
# 7. Interface do Dashboard
# ══════════════════════════════════════════════════════════════════════════════
st.title("Dashboard Coordenadores CDL Jovem - DLI 2025")
st.caption(f"Atualizado em {dt.datetime.fromtimestamp(mtime):%d/%m %H:%M}")

# ─── Filtros ───────────────────────────────────────────────────────────────────
st.sidebar.header("Filtros")

estados = sorted(df["geolocation_state_long"].unique())
estado_sel = st.sidebar.selectbox("Estado", ["Todos"] + estados, index=0)

categorias = sorted({
    c.strip()
    for lst in df["Categories"].dropna().str.split(",")
    for c in lst if c.strip()
})
cats_sel = st.sidebar.multiselect("Categorias", categorias, default=categorias)

df_f = df if estado_sel == "Todos" else df[df["geolocation_state_long"] == estado_sel]
if set(cats_sel) != set(categorias):
    df_f = df_f[df_f["Categories"].apply(
        lambda x: any(cat.strip() in cats_sel for cat in x.split(",")))]

# ─── Métricas ─────────────────────────────────────────────────────────────────
m1, m2, m3 = st.columns(3)
m1.metric("Registros", f"{len(df_f):,}")
m2.metric("Estado(s)", "Todos" if estado_sel == "Todos" else 1)
m3.metric("Categorias", len(cats_sel))

st.divider()

# ─── Gráfico por Categoria ────────────────────────────────────────────────────
st.subheader("Registros por Categoria")
cat_series = (df_f["Categories"].str.split(",").explode().str.strip()
              .replace("", pd.NA).dropna())
st.bar_chart(cat_series.value_counts())

# ─── Gráfico por Estado ───────────────────────────────────────────────────────
st.subheader("Registros por Estado")
st.bar_chart(df_f["geolocation_state_long"].value_counts())

# ─── Tabela por Cidade ────────────────────────────────────────────────────────
st.subheader("Registros por Cidade")
st.dataframe(
    df_f["geolocation_city"].value_counts()
        .rename_axis("Cidade").reset_index(name="Qtd"),
    use_container_width=True
)

st.divider()

# ─── Listagem de Lojas ────────────────────────────────────────────────────────
st.subheader("Lojas cadastradas")
query = st.text_input("Buscar loja / cidade / estado").lower().strip()

lojas = df_f.copy()
lojas["search_key"] = (
    lojas["Title"].str.lower() + " " +
    lojas["geolocation_city"].str.lower() + " " +
    lojas["geolocation_state_long"].str.lower()
)
if query:
    lojas = lojas[lojas["search_key"].str.contains(query)]

st.dataframe(
    lojas[["Title", "geolocation_city", "geolocation_state_long"]]
        .rename(columns={"Title": "Loja",
                         "geolocation_city": "Cidade",
                         "geolocation_state_long": "Estado"}),
    use_container_width=True
)

st.caption("© 2025 — Dashboard DLI 2025 · Tecnologia CNDL")
