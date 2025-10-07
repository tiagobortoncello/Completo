import streamlit as st
import sqlite3
import pandas as pd
import google.generativeai as genai
from huggingface_hub import hf_hub_download
import os

# Configurar a chave da API do Gemini a partir dos secrets
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# Configurações do Hugging Face e do dataset
HF_TOKEN = st.secrets["HF_TOKEN"]
REPO_ID = "TiagoPianezzola/BI"  # ID do repositório no Hugging Face
DB_FILENAME = "almg_local.db"  # Nome do arquivo .db no dataset

@st.cache_resource
def load_database():
    """Baixa e carrega o banco de dados SQLite do Hugging Face."""
    db_path = hf_hub_download(
        repo_id=REPO_ID,
        filename=DB_FILENAME,
        token=HF_TOKEN,
        repo_type="dataset"
    )
    return db_path

@st.cache_data
def get_schema(db_path):
    """Extrai o esquema do banco de dados (tabelas e colunas)."""
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

def generate_sql(question, schema, model):
    """Usa Gemini para gerar uma query SQL a partir da pergunta em linguagem natural."""
    prompt = f"""
    Você é um especialista em SQL. Baseado no esquema do banco de dados abaixo, gere uma query SQL válida para responder à pergunta do usuário.
    Escreva APENAS a query SQL, sem explicações adicionais. Use SELECT para consultas de leitura.
    Não use comandos DDL como CREATE ou ALTER. Use aspas duplas para nomes de colunas ou tabelas com espaços.
    Evite alucinações: baseie-se exclusivamente no esquema fornecido.

    Esquema do BD:
    {schema}

    Pergunta: {question}
    """
    
    response = model.generate_content(prompt)
    sql_query = response.text.strip()
    # Limpar se houver aspas ou código extra
    if sql_query.startswith("```sql"):
        sql_query = sql_query[6:]
    if sql_query.endswith("```"):
        sql_query = sql_query[:-3]
    sql_query = sql_query.strip()
    
    return sql_query

def execute_query(db_path, sql_query):
    """Executa a query SQL no banco de dados e retorna os resultados como DataFrame."""
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
    schema = get_schema(db_path)

# Inicializar o modelo Gemini
model = genai.GenerativeModel('gemini-2.0-flash')  # Atualizado para gemini-2.0-flash

# Input do usuário
question = st.text_input("Sua pergunta:", placeholder="Ex: Quais são os 10 deputados que mais apresentaram projetos de lei em 2023?")

if question:
    with st.spinner("Gerando query SQL..."):
        sql_query = generate_sql(question, schema, model)
        st.subheader("Query SQL Gerada:")
        st.code(sql_query, language="sql")
    
    if st.button("Executar Query"):
        with st.spinner("Executando query..."):
            results = execute_query(db_path, sql_query)
            if results is not None:
                st.subheader("Resultados:")
                st.dataframe(results)
            else:
                st.warning("Não foi possível executar a query. Verifique a sintaxe.")

# Sidebar com instruções
with st.sidebar:
    st.header("Instruções")
    st.write("""
    - Certifique-se de que o arquivo `almg_local.db` está no dataset do Hugging Face.
    - Adicione `HF_TOKEN` e `GEMINI_API_KEY` nos secrets.toml do Streamlit Cloud.
    - O app usa Gemini 2.0 Flash para conversão de linguagem natural para SQL e SQLite para execução.
    - Exemplo de pergunta: "Quais são os 5 deputados mais votados do PT?"
    """)
