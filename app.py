import streamlit as st
import sqlite3
import pandas as pd
import google.generativeai as genai
from huggingface_hub import hf_hub_download
import os

# Configurar a chave da API do Gemini a partir dos secrets
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    st.success("API do Gemini configurada com sucesso.")
except Exception as e:
    st.error(f"Erro na configuração da API do Gemini: {str(e)}. Verifique a chave nos secrets.")

# Configurações do Hugging Face e do dataset
try:
    HF_TOKEN = st.secrets["HF_TOKEN"]
    st.success("Token do Hugging Face carregado.")
except Exception as e:
    st.error(f"Erro no token do Hugging Face: {str(e)}. Verifique os secrets.")
    st.stop()

REPO_ID = "TiagoPianezzola/BI"  # ID do repositório no Hugging Face
DB_FILENAME = "almg_local.db"  # Nome do arquivo .db no dataset

@st.cache_resource
def load_database():
    """Baixa e carrega o banco de dados SQLite do Hugging Face."""
    try:
        db_path = hf_hub_download(
            repo_id=REPO_ID,
            filename=DB_FILENAME,
            token=HF_TOKEN,
            repo_type="dataset"
        )
        st.success(f"Banco de dados baixado: {db_path}")
        return db_path
    except Exception as e:
        st.error(f"Erro ao baixar o banco de dados: {str(e)}. Verifique o dataset e o token.")
        return None

@st.cache_data
def get_schema(db_path):
    """Extrai o esquema do banco de dados (tabelas e colunas)."""
    if not db_path:
        return "Erro: Banco de dados não carregado."
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Obter lista de tabelas
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    schema = []
    for table in tables:
        table_name = table[0]
        # Obter colunas da tabela
        cursor.execute(f"PRAGMA table_info({table_name});")
        columns = cursor.fetchall()
        col_info = [f"{col[1]} {col[2]}" for col in columns]  # nome tipo
        schema.append(f"Tabela: {table_name}\nColunas: {', '.join(col_info)}")
    
    conn.close()
    return "\n\n".join(schema)

def initialize_model():
    """Inicializa o modelo Gemini com fallback."""
    model_name = "gemini-1.5-flash"  # Fallback estável; mude para "gemini-2.0-flash" se confirmado
    try:
        model = genai.GenerativeModel(model_name)
        st.success(f"Modelo {model_name} inicializado com sucesso.")
        return model
    except Exception as e:
        st.error(f"Erro ao inicializar o modelo {model_name}: {str(e)}. Tentando fallback...")
        # Fallback para um modelo mais básico se necessário
        try:
            model = genai.GenerativeModel('gemini-pro')
            st.warning("Usando modelo fallback: gemini-pro.")
            return model
        except Exception as e2:
            st.error(f"Fallback falhou: {str(e2)}. Verifique a chave da API.")
            return None

def generate_sql(question, schema, model):
    """Usa Gemini para gerar uma query SQL a partir da pergunta em linguagem natural."""
    if not model:
        return "Erro: Modelo não disponível."
    
    prompt = f"""
    Você é um especialista em SQL. Baseado no esquema do banco de dados abaixo, gere uma query SQL válida para responder à pergunta do usuário.
    Escreva APENAS a query SQL, sem explicações adicionais. Use SELECT para consultas de leitura.
    Não use comandos DDL como CREATE ou ALTER. Use aspas duplas para nomes de colunas ou tabelas com espaços.
    Evite alucinações: baseie-se exclusivamente no esquema fornecido. Limite resultados a 10-20 linhas se possível.

    Esquema do BD:
    {schema}

    Pergunta: {question}
    """
    
    try:
        response = model.generate_content(prompt)
        sql_query = response.text.strip()
        # Limpar se houver aspas ou código extra
        if sql_query.startswith("```sql"):
            sql_query = sql_query[6:]
        if sql_query.endswith("```"):
            sql_query = sql_query[:-3]
        sql_query = sql_query.strip()
        
        return sql_query
    except Exception as e:
        return f"Erro na geração da query: {str(e)}"

def execute_query(db_path, sql_query):
    """Executa a query SQL no banco de dados e retorna os resultados como DataFrame."""
    if not db_path:
        st.error("Banco de dados não disponível.")
        return None
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(sql_query, conn)
        return df
    except Exception as e:
        st.error(f"Erro ao executar a query: {str(e)}")
        return None
    finally:
        conn.close()

# Interface do Streamlit
st.title("Assistente de Consulta SQL com IA - ALMG")
st.write("Digite uma pergunta em linguagem natural sobre os dados da Assembleia Legislativa de Minas Gerais, e o app gerará e executará a query SQL.")

# Carregar DB e esquema
with st.spinner("Carregando banco de dados..."):
    db_path = load_database()
    if db_path:
        schema = get_schema(db_path)
    else:
        schema = None
        st.stop()  # Para a execução se DB falhar

# Inicializar o modelo Gemini
if 'model' not in st.session_state:
    st.session_state.model = None

if st.button("Recarregar Modelo Gemini") or st.session_state.model is None:
    st.session_state.model = initialize_model()

model = st.session_state.model

# Input do usuário
question = st.text_input("Sua pergunta:", placeholder="Ex: Quais são os 10 deputados que mais apresentaram projetos de lei em 2023?")

if question and model:
    with st.spinner("Gerando query SQL..."):
        sql_query = generate_sql(question, schema, model)
        st.subheader("Query SQL Gerada:")
        st.code(sql_query, language="sql")
    
    if st.button("Executar Query"):
        with st.spinner("Executando query..."):
            results = execute_query(db_path, sql_query)
            if results is not None and not results.empty:
                st.subheader("Resultados:")
                st.dataframe(results)
            elif results is not None:
                st.info("Query executada, mas sem resultados.")
            else:
                st.warning("Não foi possível executar a query. Verifique a sintaxe.")

# Sidebar com instruções e debug
with st.sidebar:
    st.header("Instruções e Debug")
    st.write("""
    - Certifique-se de que `HF_TOKEN` e `GEMINI_API_KEY` estão nos secrets.toml.
    - O app usa Gemini 1.5 Flash (fallback estável). Para 2.0 Flash, teste localmente primeiro.
    - Exemplo: "Quais são os 5 deputados mais votados do PT?"
    """)
    
    if st.checkbox("Mostrar Esquema do DB (Debug)"):
        st.text_area("Esquema:", schema or "Não disponível", height=300)
    
    st.info("Se o app não carrega, verifique os logs no Streamlit Cloud.")
