import sqlite3
import pandas as pd
import streamlit as st
import plotly.express as px
from pathlib import Path
import os
from dotenv import set_key, load_dotenv

# Configurações do Streamlit
st.set_page_config(
    page_title="LHAM Dashboard",
    page_icon="🟠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for styling
st.markdown("""
<style>
    .stApp {
        background-color: #0E1117;
    }
    .main-header {
        font-family: 'Inter', sans-serif;
        color: #FFFFFF;
        font-weight: 700;
        margin-bottom: 30px;
        background: -webkit-linear-gradient(45deg, #FF4B2B, #FF416C);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .kpi-card {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        text-align: center;
        transition: transform 0.3s ease;
    }
    .kpi-card:hover {
        transform: translateY(-5px);
    }
    .kpi-value {
        font-size: 2.5rem;
        font-weight: 700;
        color: #00E676;
    }
    .kpi-label {
        font-size: 1rem;
        color: #B0BEC5;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    .section-title {
        font-family: 'Inter', sans-serif;
        color: #E0E0E0;
        font-weight: 600;
        margin-top: 40px;
        margin-bottom: 20px;
        border-bottom: 2px solid #FF416C;
        display: inline-block;
        padding-bottom: 5px;
    }
</style>
""", unsafe_allow_html=True)

# Define paths
DB_PATH = Path("./data/bot.db")
ENV_PATH = Path(".env")

# Helper to load data
@st.cache_data(ttl=60)
def load_data(query: str):
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(query, conn)

def main():
    st.sidebar.title("🏗️ LHAM Control Center")
    st.sidebar.markdown("---")
    
    page = st.sidebar.radio(
        "Navegação",
        ["Overview", "Interações", "Uso e Custos", "Usuários & Projetos", "Configurações"]
    )
    
    st.sidebar.markdown("---")
    st.sidebar.info("Dashboard administrativo para monitoramento e gestão do sistema Telegram-RAG.")
    
    if not DB_PATH.exists():
        st.error(f"Banco de dados não encontrado em {DB_PATH.resolve()}")
        return

    if page == "Overview":
        render_overview()
    elif page == "Interações":
        render_interactions()
    elif page == "Uso e Custos":
        render_usage_costs()
    elif page == "Usuários & Projetos":
        render_users_projects()
    elif page == "Configurações":
        render_configurations()

def render_overview():
    st.markdown('<h1 class="main-header">Visão Geral do Sistema</h1>', unsafe_allow_html=True)
    
    # Load KPIs
    interactions_count = load_data("SELECT COUNT(*) as count FROM interactions").iloc[0]['count']
    users_count = load_data("SELECT COUNT(*) as count FROM users").iloc[0]['count']
    projects_count = load_data("SELECT COUNT(*) as count FROM projects").iloc[0]['count']
    tokens_total = load_data("SELECT SUM(total_tokens) as count FROM token_usage").iloc[0]['count']
    tokens_total = tokens_total if pd.notna(tokens_total) else 0
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'''
            <div class="kpi-card">
                <div class="kpi-value">{interactions_count:,}</div>
                <div class="kpi-label">Interações</div>
            </div>
        ''', unsafe_allow_html=True)
    with col2:
        st.markdown(f'''
            <div class="kpi-card">
                <div class="kpi-value">{users_count:,}</div>
                <div class="kpi-label">Usuários</div>
            </div>
        ''', unsafe_allow_html=True)
    with col3:
        st.markdown(f'''
            <div class="kpi-card">
                <div class="kpi-value">{projects_count:,}</div>
                <div class="kpi-label">Projetos</div>
            </div>
        ''', unsafe_allow_html=True)
    with col4:
        st.markdown(f'''
            <div class="kpi-card">
                <div class="kpi-value">{int(tokens_total):,}</div>
                <div class="kpi-label">Tokens Usados</div>
            </div>
        ''', unsafe_allow_html=True)
        
    st.markdown('<h2 class="section-title">Atividade Recente</h2>', unsafe_allow_html=True)
    recent_interactions = load_data("""
        SELECT timestamp, user_message, model_used, total_duration_ms 
        FROM interactions 
        ORDER BY id DESC LIMIT 10
    """)
    if not recent_interactions.empty:
        st.dataframe(recent_interactions, use_container_width=True)
    else:
        st.info("Nenhuma interação registrada ainda.")

def render_interactions():
    st.markdown('<h1 class="main-header">Interações RAG</h1>', unsafe_allow_html=True)
    
    df = load_data("SELECT * FROM interactions ORDER BY id DESC LIMIT 1000")
    if df.empty:
        st.info("Nenhum dado disponível.")
        return
        
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Interações por dia
    st.markdown('<h2 class="section-title">Volume de Mensagens</h2>', unsafe_allow_html=True)
    daily_counts = df.resample('D', on='timestamp').size().reset_index(name='count')
    fig = px.line(daily_counts, x='timestamp', y='count', title="Interações Diárias", template="plotly_dark", line_shape="spline")
    fig.update_traces(line_color="#FF416C", line_width=3)
    st.plotly_chart(fig, use_container_width=True)
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown('<h2 class="section-title">Modelos Utilizados</h2>', unsafe_allow_html=True)
        model_counts = df['model_used'].value_counts().reset_index()
        fig_pie = px.pie(model_counts, values='count', names='model_used', template="plotly_dark", hole=0.4)
        st.plotly_chart(fig_pie, use_container_width=True)
        
    with col2:
        st.markdown('<h2 class="section-title">Tempo de Resposta (ms)</h2>', unsafe_allow_html=True)
        fig_box = px.box(df, y='total_duration_ms', color='model_used', template="plotly_dark")
        st.plotly_chart(fig_box, use_container_width=True)

def render_usage_costs():
    st.markdown('<h1 class="main-header">Monitoramento de Uso e Custos</h1>', unsafe_allow_html=True)
    
    df = load_data("SELECT * FROM token_usage ORDER BY id DESC")
    if df.empty:
        st.info("Nenhum dado de uso disponível.")
        return
        
    df['created_at'] = pd.to_datetime(df['created_at'])
    df['date'] = df['created_at'].dt.date
    
    daily_tokens = df.groupby('date')['total_tokens'].sum().reset_index()
    
    st.markdown('<h2 class="section-title">Uso Diário de Tokens</h2>', unsafe_allow_html=True)
    fig_bar = px.bar(daily_tokens, x='date', y='total_tokens', template="plotly_dark", color_discrete_sequence=['#00E676'])
    st.plotly_chart(fig_bar, use_container_width=True)
    
    st.markdown('<h2 class="section-title">Tokens por Modelo</h2>', unsafe_allow_html=True)
    model_tokens = df.groupby('model')['total_tokens'].sum().reset_index()
    fig_model = px.bar(model_tokens, x='model', y='total_tokens', template="plotly_dark", color_discrete_sequence=['#2196F3'])
    st.plotly_chart(fig_model, use_container_width=True)

def render_users_projects():
    st.markdown('<h1 class="main-header">Usuários e Projetos</h1>', unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["Usuários", "Projetos"])
    
    with tab1:
        users_df = load_data("SELECT id, name, telegram_id, role, status, created_at FROM users")
        st.dataframe(users_df, use_container_width=True)
        
    with tab2:
        projects_df = load_data("SELECT id, name, status, start_date, end_date, created_at FROM projects")
        st.dataframe(projects_df, use_container_width=True)

def render_configurations():
    st.markdown('<h1 class="main-header">Configurações do Sistema</h1>', unsafe_allow_html=True)
    
    st.info("Altere as variáveis de ambiente diretamente pelo painel. Algumas mudanças requerem reinicialização do bot.")
    
    load_dotenv(ENV_PATH)
    
    with st.form("config_form"):
        st.markdown('<h2 class="section-title">Modelos LLM</h2>', unsafe_allow_html=True)
        
        ollama_model = st.text_input("Modelo Padrão (Ollama)", value=os.environ.get("OLLAMA_DEFAULT_MODEL", "gemma:2b"))
        embedding_model = st.text_input("Modelo de Embedding (Ollama)", value=os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"))
        fallback_models = st.text_input("Modelos de Fallback (CSV)", value=os.environ.get("CHAT_FALLBACK_MODELS", ""))
        
        st.markdown('<h2 class="section-title">Configurações de RAG</h2>', unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        with col1:
            rag_top_k = st.number_input("Top K (Recuperação)", value=int(os.environ.get("RAG_TOP_K", "20")))
            chunk_size = st.number_input("Tamanho do Chunk", value=int(os.environ.get("CHUNK_SIZE", "2500")))
        with col2:
            rag_recent_history = st.number_input("Histórico Recente (mensagens)", value=int(os.environ.get("RAG_RECENT_HISTORY", "6")))
            chunk_overlap = st.number_input("Overlap do Chunk", value=int(os.environ.get("CHUNK_OVERLAP", "300")))
            
        submitted = st.form_submit_button("Salvar Configurações", type="primary")
        if submitted:
            set_key(ENV_PATH, "OLLAMA_DEFAULT_MODEL", ollama_model)
            set_key(ENV_PATH, "OLLAMA_EMBEDDING_MODEL", embedding_model)
            set_key(ENV_PATH, "CHAT_FALLBACK_MODELS", fallback_models)
            set_key(ENV_PATH, "RAG_TOP_K", str(rag_top_k))
            set_key(ENV_PATH, "CHUNK_SIZE", str(chunk_size))
            set_key(ENV_PATH, "RAG_RECENT_HISTORY", str(rag_recent_history))
            set_key(ENV_PATH, "CHUNK_OVERLAP", str(chunk_overlap))
            
            st.success("Configurações atualizadas no arquivo .env com sucesso!")

if __name__ == "__main__":
    main()
