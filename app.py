# -*- coding: utf-8 -*-
import streamlit as st
import re
import pandas as pd
import pypdf
import io
import csv
import fitz  # PyMuPDF
import requests
import pdfplumber
import json
from datetime import datetime, timedelta, date
import os
import docx
import subprocess
import tempfile
import shutil

# --- Constantes e Mapeamentos para Extrator de Diários Oficiais ---
TIPO_MAP_NORMA = {
    "LEI": "LEI",
    "RESOLUÇÃO": "RAL",
    "LEI COMPLEMENTAR": "LCP",
    "EMENDA À CONSTITUIÇÃO": "EMC",
    "DELIBERAÇÃO DA MESA": "DLB"
}

TIPO_MAP_PROP = {
    "PROJETO DE LEI": "PL",
    "PROJETO DE LEI COMPLEMENTAR": "PLC",
    "INDICAÇÃO": "IND",
    "PROJETO DE RESOLUÇÃO": "PRE",
    "PROPOSTA DE EMENDA À CONSTITUIÇÃO": "PEC",
    "MENSAGEM": "MSG",
    "VETO": "VET"
}

SIGLA_MAP_PARECER = {
    "requerimento": "RQN",
    "projeto de lei": "PL",
    "pl": "PL",
    "projeto de resolução": "PRE",
    "pre": "PRE",
    "proposta de emenda à constituição": "PEC",
    "pec": "PEC",
    "projeto de lei complementar": "PLC",
    "plc": "PLC",
    "emendas ao projeto de lei": "EMENDA"
}

meses = {
    "JANEIRO": "01", "FEVEREIRO": "02", "MARÇO": "03", "MARCO": "03",
    "ABRIL": "04", "MAIO": "05", "JUNHO": "06", "JULHO": "07",
    "AGOSTO": "08", "SETEMBRO": "09", "OUTUBRO": "10", "NOVEMBRO": "11", "DEZEMBRO": "12"
}

# --- Funções Utilitárias para Extrator de Diários Oficiais ---
def classify_req(segment: str) -> str:
    segment_lower = segment.lower()
    if "seja formulado voto de congratulações" in segment_lower:
        return "Voto de congratulações"
    if "manifestação de pesar" in segment_lower:
        return "Manifestação de pesar"
    if "manifestação de repúdio" in segment_lower:
        return "Manifestação de repúdio"
    if "moção de aplauso" in segment_lower:
        return "Moção de aplauso"
    if "r seja formulada manifestação de apoio" in segment_lower:
        return "Manifestação de apoio"
    return ""

# --- Classes de Processamento para Extrator de Diários Oficiais ---
class LegislativeProcessor:
    def __init__(self, text: str):
        self.text = text

    def process_normas(self) -> list: # Mudança no tipo de retorno
        pattern = re.compile(
            r"^(LEI COMPLEMENTAR|LEI|RESOLUÇÃO|EMENDA À CONSTITUIÇÃO|DELIBERAÇÃO DA MESA) Nº (\d{1,5}(?:\.\d{0,3})?)(?:/(\d{4}))?(?:, DE .+ DE (\d{4}))?$",
            re.MULTILINE
        )
        normas = []
        for match in pattern.finditer(self.text):
            tipo_extenso = match.group(1)
            numero_raw = match.group(2).replace(".", "")
            ano = match.group(3) if match.group(3) else match.group(4)
            if not ano:
                continue
            sigla = TIPO_MAP_NORMA[tipo_extenso]
            normas.append([sigla, numero_raw, ano])
        return normas # Retorna a lista, sem criar o DataFrame

    def process_proposicoes(self) -> list: # Mudança no tipo de retorno
        pattern_prop = re.compile(
            r"^\s*(?:- )?\s*(PROJETO DE LEI COMPLEMENTAR|PROJETO DE LEI|INDICAÇÃO|PROJETO DE RESOLUÇÃO|PROPOSTA DE EMENDA À CONSTITUIÇÃO|MENSAGEM|VETO) Nº (\d{1,4}\.?\d{0,3}/\d{4})",
            re.MULTILINE
        )
        pattern_utilidade = re.compile(r"Declara de utilidade pública", re.IGNORECASE | re.DOTALL)
        ignore_redacao_final = re.compile(r"opinamos por se dar à proposição a seguinte redação final", re.IGNORECASE)
        ignore_publicada_antes = re.compile(r"foi publicad[ao] na edição anterior\.", re.IGNORECASE)
        ignore_em_epigrafe = re.compile(r"Na publicação da matéria em epígrafe", re.IGNORECASE)

        proposicoes = []
        for match in pattern_prop.finditer(self.text):
            start_idx = match.start()
            end_idx = match.end()
            contexto_antes = self.text[max(0, start_idx - 200):start_idx]
            contexto_depois = self.text[end_idx:end_idx + 250]

            if ignore_em_epigrafe.search(contexto_depois):
                continue
            if ignore_redacao_final.search(contexto_antes) or ignore_publicada_antes.search(contexto_depois):
                continue
            subseq_text = self.text[end_idx:end_idx + 250]
            if "(Redação do Vencido)" in subseq_text:
                continue

            tipo_extenso = match.group(1)
            numero_ano = match.group(2).replace(".", "")
            numero, ano = numero_ano.split("/")
            sigla = TIPO_MAP_PROP[tipo_extenso]
            categoria = "UP" if pattern_utilidade.search(subseq_text) else ""
            proposicoes.append([sigla, numero, ano, categoria])

        return proposicoes # Retorna a lista, sem criar o DataFrame

    def process_requerimentos(self) -> list: # Mudança no tipo de retorno
        requerimentos = []
        ignore_pattern = re.compile(
            r"Ofício nº .*?,.*?relativas ao Requerimento\s*nº (\d{1,4}\.?\d{0,3}/\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        aprovado_pattern = re.compile(
            r"(da Comissão.*?, informando que, na.*?foi aprovado o Requerimento\s*nº (\d{1,5}(?:\.\d{0,3})?)/(\d{4}))",
            re.IGNORECASE | re.DOTALL
        )
        reqs_to_ignore = set()
        for match in ignore_pattern.finditer(self.text):
            numero_ano = match.group(1).replace(".", "")
            reqs_to_ignore.add(numero_ano)

        for match in aprovado_pattern.finditer(self.text):
            num_part = match.group(2).replace('.', '')
            ano = match.group(3)
            numero_ano = f"{num_part}/{ano}"
            reqs_to_ignore.add(numero_ano)

        req_recebimento_pattern = re.compile(
            r"RECEBIMENTO DE PROPOSIÇÃO[\s\S]*?REQUERIMENTO Nº (\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in req_recebimento_pattern.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQN", num_part, ano, "", "", "Recebido"])

        rqc_pattern_aprovado = re.compile(
            r"É\s+recebido\s+pela\s+presidência,\s+submetido\s+a\s+votação\s+e\s+aprovado\s+o\s+Requerimento(?:s)?(?: nº| Nº| n\u00ba| n\u00b0)?\s*(\d{1,5}(?:\.\d{0,3})?)/\s*(\d{4})",
            re.IGNORECASE
        )
        for match in rqc_pattern_aprovado.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Aprovado"])

        rqc_recebido_apreciacao_pattern = re.compile(
            r"É recebido pela\s+presidência, para posterior apreciação, o Requerimento(?: nº| Nº)?\s*(\d{1,5}(?:\.\d{0,3})?)/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in rqc_recebido_apreciacao_pattern.finditer(self.text):
            num_part = match.group(1).replace('.', '')
            ano = match.group(2)
            numero_ano = f"{num_part}/{ano}"
            if numero_ano not in reqs_to_ignore:
                requerimentos.append(["RQC", num_part, ano, "", "", "Recebido para apreciação"])

        rqn_pattern = re.compile(r"^(?:\s*)(Nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
        rqc_old_pattern = re.compile(r"^(?:\s*)(nº)\s+(\d{2}\.?\d{3}/\d{4})\s*,\s*(do|da)", re.MULTILINE)
        for pattern, sigla_prefix in [(rqn_pattern, "RQN"), (rqc_old_pattern, "RQC")]:
            for match in pattern.finditer(self.text):
                start_idx = match.start()
                next_match = re.search(r"^(?:\s*)(Nº|nº)\s+(\d{2}\.?\d{3}/\d{4})", self.text[start_idx + 1:], flags=re.MULTILINE)
                end_idx = (next_match.start() + start_idx + 1) if next_match else len(self.text)
                block = self.text[start_idx:end_idx].strip()
                nums_in_block = re.findall(r'\d{2}\.?\d{3}/\d{4}', block)
                if not nums_in_block:
                    continue
                num_part, ano = nums_in_block[0].replace(".", "").split("/")
                numero_ano = f"{num_part}/{ano}"
                if numero_ano not in reqs_to_ignore:
                    classif = classify_req(block)
                    requerimentos.append([sigla_prefix, num_part, ano, "", "", classif])

        nao_recebidas_header_pattern = re.compile(r"PROPOSIÇÕES\s*NÃO\s*RECEBIDAS", re.IGNORECASE)
        header_match = nao_recebidas_header_pattern.search(self.text)
        if header_match:
            start_idx = header_match.end()
            next_section_pattern = re.compile(r"^\s*(\*?)\s*.*\s*(\*?)\s*$", re.MULTILINE)
            next_section_match = next_section_pattern.search(self.text, start_idx)
            end_idx = next_section_match.start() if next_section_match else len(self.text)
            nao_recebidos_block = self.text[start_idx:end_idx]
            rqn_nao_recebido_pattern = re.compile(r"REQUERIMENTO Nº (\d{2}\.?\d{3}/\d{4})", re.IGNORECASE)
            for match in rqn_nao_recebido_pattern.finditer(nao_recebidos_block):
                numero_ano = match.group(1).replace(".", "")
                num_part, ano = numero_ano.split("/")
                if numero_ano not in reqs_to_ignore:
                    requerimentos.append(["RQN", num_part, ano, "", "", "NÃO RECEBIDO"])

        unique_reqs = []
        seen = set()
        for r in requerimentos:
            key = (r[0], r[1], r[2])
            if key not in seen:
                seen.add(key)
                unique_reqs.append(r)

        return unique_reqs # Retorna a lista, sem criar o DataFrame

    def process_pareceres(self) -> list: # Mudança no tipo de retorno
        found_projects = {}
        pareceres_start_pattern = re.compile(r"TRAMITAÇÃO DE PROPOSIÇÕES")
        votacao_pattern = re.compile(
            r"(Votação do Requerimento[\s\S]*?)(?=Votação do Requerimento|Diário do Legislativo|Projetos de Lei Complementar|Diário do Legislativo - Poder Legislativo|$)",
            re.IGNORECASE
        )
        pareceres_start = pareceres_start_pattern.search(self.text)
        if not pareceres_start:
            return [] # Retorna lista vazia

        pareceres_text = self.text[pareceres_start.end():]
        clean_text = pareceres_text
        for match in votacao_pattern.finditer(pareceres_text):
            clean_text = clean_text.replace(match.group(0), "")

        emenda_projeto_lei_pattern = re.compile(
            r"EMENDAS AO PROJETO DE LEI Nº (\d{1,4}\.?\d{0,3})/(\d{4})",
            re.IGNORECASE | re.DOTALL
        )
        for match in emenda_projeto_lei_pattern.finditer(clean_text):
            numero_raw = match.group(1).replace('.', '')
            ano = match.group(2)
            project_key = ("PL", numero_raw, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        emenda_completa_pattern = re.compile(
            r"EMENDA Nº (\d+)\s+AO\s+(?:SUBSTITUTIVO Nº \d+\s+AO\s+)?PROJETO DE LEI(?: COMPLEMENTAR)? Nº (\d{1,4}\.?\d{0,3})/(\d{4})",
            re.IGNORECASE
        )
        emenda_pattern = re.compile(r"^(?:\s*)EMENDA Nº (\d+)\s*", re.MULTILINE)
        substitutivo_pattern = re.compile(r"^(?:\s*)SUBSTITUTIVO Nº (\d+)\s*", re.MULTILINE)
        project_pattern = re.compile(
            r"Conclusão\s*([\s\S]*?)(Projeto de Lei|PL|Projeto de Resolução|PRE|Proposta de Emenda à Constituição|PEC|Projeto de Lei Complementar|PLC|Requerimento)\s+(?:nº|Nº)?\s*(\d{1,4}(?:\.\d{1,3})?)\s*/\s*(\d{4})",
            re.IGNORECASE | re.DOTALL
        )

        for match in emenda_completa_pattern.finditer(clean_text):
            numero = match.group(2).replace(".", "")
            ano = match.group(3)
            sigla = "PLC" if "COMPLEMENTAR" in match.group(0).upper() else "PL"
            project_key = (sigla, numero, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        all_matches = sorted(
            list(emenda_pattern.finditer(clean_text)) + list(substitutivo_pattern.finditer(clean_text)),
            key=lambda x: x.start()
        )

        for title_match in all_matches:
            text_before_title = clean_text[:title_match.start()]
            last_project_match = None
            for match in project_pattern.finditer(text_before_title):
                last_project_match = match

            if last_project_match:
                sigla_raw = last_project_match.group(2)
                sigla = SIGLA_MAP_PARECER.get(sigla_raw.lower(), sigla_raw.upper())
                numero = last_project_match.group(3).replace(".", "")
                ano = last_project_match.group(4)
                project_key = (sigla, numero, ano)
                item_type = "EMENDA" if "EMENDA" in title_match.group(0).upper() else "SUBSTITUTIVO"
                if project_key not in found_projects:
                    found_projects[project_key] = set()
                found_projects[project_key].add(item_type)

        emenda_projeto_lei_pattern = re.compile(r"EMENDAS AO PROJETO DE LEI Nº (\d{1,4}\.?\d{0,3})/(\d{4})", re.IGNORECASE)
        for match in emenda_projeto_lei_pattern.finditer(clean_text):
            numero_raw = match.group(1).replace('.', '')
            ano = match.group(2)
            project_key = ("PL", numero_raw, ano)
            if project_key not in found_projects:
                found_projects[project_key] = set()
            found_projects[project_key].add("EMENDA")

        pareceres = []
        for (sigla, numero, ano), types in found_projects.items():
            type_str = "SUB/EMENDA" if len(types) > 1 else list(types)[0]
            pareceres.append([sigla, numero, ano, type_str])

        return pareceres # Retorna a lista, sem criar o DataFrame

    def process_all(self) -> dict:
        # A função process_all agora retorna listas de dados em vez de DataFrames
        df_normas = self.process_normas()
        df_proposicoes = self.process_proposicoes()
        df_requerimentos = self.process_requerimentos()
        df_pareceres = self.process_pareceres()
        return {
            "Normas": df_normas,
            "Proposicoes": df_proposicoes,
            "Requerimentos": df_requerimentos,
            "Pareceres": df_pareceres
        }
class AdministrativeProcessor:
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes

    def process_pdf(self):
        try:
            doc = fitz.open(stream=self.pdf_bytes, filetype="pdf")
        except Exception as e:
            st.error(f"Erro ao abrir o arquivo PDF: {e}")
            return None

        resultados = []
        regex = re.compile(
            r'(DELIBERAÇÃO DA MESA|PORTARIA DGE|ORDEM DE SERVIÇO PRES/PSEC)\s+Nº\s+([\d\.]+)\/(\d{4})'
        )
        regex_dcs = re.compile(r'DECIS[ÃA]O DA 1ª-SECRETARIA')

        for page in doc:
            text = page.get_text("text")
            text = re.sub(r'\s+', ' ', text)
            for match in regex.finditer(text):
                tipo_texto = match.group(1)
                numero = match.group(2).replace('.', '')
                ano = match.group(3)
                sigla = {
                    "DELIBERAÇÃO DA MESA": "DLB",
                    "PORTARIA DGE": "PRT",
                    "ORDEM DE SERVIÇO PRES/PSEC": "OSV"
                }.get(tipo_texto, None)
                if sigla:
                    resultados.append([sigla, numero, ano])
            if regex_dcs.search(text):
                resultados.append(["DCS", "", ""])
        doc.close()
        return pd.DataFrame(resultados, columns=['Sigla', 'Número', 'Ano'])

    def to_csv(self):
        df = self.process_pdf()
        if df.empty:
            return None
        output_csv = io.StringIO()
        df.to_csv(output_csv, index=False, encoding="utf-8-sig")
        return output_csv.getvalue().encode('utf-8')

class ExecutiveProcessor:
    def __init__(self, pdf_bytes: bytes):
        self.pdf_bytes = pdf_bytes
        self.mapa_tipos = {
            "LEI": "LEI",
            "LEI COMPLEMENTAR": "LCP",
            "DECRETO": "DEC",
            "DECRETO NE": "DNE"
        }
        self.norma_regex = re.compile(
            r'\b(LEI\s+COMPLEMENTAR|LEI|DECRETO\s+NE|DECRETO)\s+N[º°]\s*([\d\s\.]+),\s*DE\s+([A-Z\s\d]+)\b'
        )
        self.comandos_regex = re.compile(
            r'(Ficam\s+revogados|Fica\s+acrescentado|Ficam\s+alterados|passando\s+o\s+item|passa\s+a\s+vigorar|passam\s+a\s+vigorar)',
            re.IGNORECASE
        )
        self.norma_alterada_regex = re.compile(
            r'(LEI\s+COMPLEMENTAR|LEI|DECRETO\s+NE|DECRETO)\s+N[º°]?\s*([\d\s\./]+)(?:,\s*de\s*(.*?\d{4})?)?',
            re.IGNORECASE
        )

    def find_relevant_pages(self) -> tuple:
        try:
            reader = pypdf.PdfReader(io.BytesIO(self.pdf_bytes))
            start_page_num, end_page_num = None, None

            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if not text.strip():
                    continue
                if re.search(r'Leis\s*e\s*Decretos', text, re.IGNORECASE):
                    start_page_num = i
                if re.search(r'Atos\s*do\s*Governador', text, re.IGNORECASE):
                    end_page_num = i

            if start_page_num is None or end_page_num is None or start_page_num > end_page_num:
                st.warning("Não foi encontrado o trecho de 'Leis e Decretos' ou 'Atos do Governador' para delimitar a seção.")
                return None, None

            return start_page_num, end_page_num + 1

        except Exception as e:
            st.error(f"Erro ao buscar páginas relevantes com PyPDF: {e}")
            return None, None

    def process_pdf(self) -> pd.DataFrame:
        start_page_idx, end_page_idx = self.find_relevant_pages()
        if start_page_idx is None:
            return pd.DataFrame()

        trechos = []
        try:
            with pdfplumber.open(io.BytesIO(self.pdf_bytes)) as pdf:
                for i in range(start_page_idx, end_page_idx):
                    pagina = pdf.pages[i]
                    largura, altura = pagina.width, pagina.height
                    for col_num, (x0, x1) in enumerate([(0, largura/2), (largura/2, largura)], start=1):
                        coluna = pagina.crop((x0, 0, x1, altura)).extract_text(layout=True) or ""
                        texto_limpo = re.sub(r'\s+', ' ', coluna).strip()
                        trechos.append({
                            "pagina": i + 1,
                            "coluna": col_num,
                            "texto": texto_limpo
                        })
        except Exception as e:
            st.error(f"Erro ao extrair texto detalhado do PDF do Executivo: {e}")
            return pd.DataFrame()

        dados = []
        ultima_norma = None
        seen_alteracoes = set()

        for t in trechos:
            pagina = t["pagina"]
            coluna = t["coluna"]
            texto = t["texto"]

            eventos = []
            for m in self.norma_regex.finditer(texto):
                eventos.append(('published', m.start(), m))
            for c in self.comandos_regex.finditer(texto):
                eventos.append(('command', c.start(), c))
            eventos.sort(key=lambda e: e[1])

            for ev in eventos:
                tipo_ev, pos_ev, match_obj = ev
                command_text = match_obj.group(0).lower()

                if tipo_ev == 'published':
                    match = match_obj
                    tipo_raw = match.group(1).strip()
                    tipo = self.mapa_tipos.get(tipo_raw.upper(), tipo_raw)
                    numero = match.group(2).replace(" ", "").replace(".", "")
                    data_texto = match.group(3).strip()

                    try:
                        partes = data_texto.split(" DE ")
                        dia = partes[0].zfill(2)
                        mes = meses[partes[1].upper()]
                        ano = partes[2]
                        sancao = f"{dia}/{mes}/{ano}"
                    except:
                        sancao = ""

                    linha = {
                        "Página": pagina,
                        "Coluna": coluna,
                        "Sanção": sancao,
                        "Tipo": tipo,
                        "Número": numero,
                        "Alterações": ""
                    }
                    dados.append(linha)
                    ultima_norma = linha
                    seen_alteracoes = set()

                elif tipo_ev == 'command':
                    if ultima_norma is None:
                        continue

                    raio = 150
                    start_block = max(0, pos_ev - raio)
                    end_block = min(len(texto), pos_ev + raio)
                    bloco = texto[start_block:end_block]

                    alteracoes_para_processar = []
                    if 'revogado' in command_text:
                        alteracoes_para_processar = list(self.norma_alterada_regex.finditer(bloco))
                    else:
                        alteracoes_candidatas = list(self.norma_alterada_regex.finditer(bloco))
                        if alteracoes_candidatas:
                            pos_comando_no_bloco = pos_ev - start_block
                            melhor_candidato = min(
                                alteracoes_candidatas,
                                key=lambda m: abs(m.start() - pos_comando_no_bloco)
                            )
                            alteracoes_para_processar = [melhor_candidato]

                    for alt in alteracoes_para_processar:
                        tipo_alt_raw = alt.group(1).strip()
                        tipo_alt = self.mapa_tipos.get(tipo_alt_raw.upper(), tipo_alt_raw)
                        num_alt = alt.group(2).replace(" ", "").replace(".", "").replace("/", "")

                        data_texto_alt = alt.group(3)
                        ano_alt = ""
                        if data_texto_alt:
                            ano_match = re.search(r'(\d{4})', data_texto_alt)
                            if ano_match:
                                ano_alt = ano_match.group(1)

                        chave_alt = f"{tipo_alt} {num_alt}"
                        if ano_alt:
                            chave_alt += f" {ano_alt}"

                        if tipo_alt == ultima_norma["Tipo"] and num_alt == ultima_norma["Número"]:
                            continue

                        if chave_alt in seen_alteracoes:
                            continue
                        seen_alteracoes.add(chave_alt)

                        if ultima_norma["Alterações"] == "":
                            ultima_norma["Alterações"] = chave_alt
                        else:
                            dados.append({
                                "Página": "",
                                "Coluna": "",
                                "Sanção": "",
                                "Tipo": "",
                                "Número": "",
                                "Alterações": chave_alt
                            })

        return pd.DataFrame(dados) if dados else pd.DataFrame()

    def to_csv(self):
        df = self.process_pdf()
        if df.empty:
            return None
        output_csv = io.StringIO()
        df.to_csv(output_csv, index=False, encoding="utf-8-sig")
        return output_csv.getvalue().encode('utf-8')

# --- Funções para Gerador de Links ---
def dia_anterior():
    st.session_state.data -= timedelta(days=1)

def dia_posterior():
    st.session_state.data += timedelta(days=1)

def ir_hoje():
    st.session_state.data = datetime.today().date()

# --- Funções para Chatbot ---
DOCUMENTOS_PRE_CARREGADOS = {
    "Manual de Indexação": "manual_indexacao.pdf",
    "Regimento Interno da ALMG": "regimento.pdf",
    "Constituição Estadual": "constituicao.pdf",
    "Manual de redação parlamentar": "manual_redacao.pdf",
}

PROMPTS_POR_DOCUMENTO = {
    "Manual de Indexação": """
Personalização da IA:
Você deve atuar como um bibliotecário da Assembleia Legislativa do Estado de Minas Gerais, que tira dúvidas sobre como devem ser indexados os documentos legislativos com base no documento Conhecimento Manual de Indexação 4ª ed.-2023.docx.

====================================================================

Tarefa principal:
A partir do documento, você deve auxiliar o bibliotecário localizado as regras de indexação e resumo dos documentos legislativos.

====================================================================

Regras específicas:
Não consulte nenhum outro documento. 
Se não entender a pergunta ou não localizar a resposta, responda que não é possível responder a solicitação, pois não está prevista no Manual de Indexação.
O documento está estruturado em seções. Os exemplos vêm dentro de quadros. Você deve sugerir os termos de indexação conforme os exemplos, usando somente os termos mais específicos.
Você deve apresentar somente os termos mais específicos da indexação. Se o campo resumo estiver preenchido com #, significa que aquele tipo não precisa de resumo.
Caso ele esteja preenchido, você deve informar que ele deve ter resumo e mostrar o exemplo do resumo.
Sempre que achar a resposta, você deve primeiro listar os termos de indexação relevantes de maneira mais explícita, indicando a informação que será indexada. Por exemplo: "Para indexar [informação que vem na pergunta], você deve utilizar os seguintes termos:". Em seguida, liste os termos.
Depois, reproduza o quadro de exemplo correspondente, precedido da frase "Confira o exemplo a seguir:", e a resposta deve ser fechada com a seguinte citação da página, sem aspas:

"Você pode verificar a informação na página [cite a página] do Manual de Indexação."

Confira o exemplo a seguir:

| Tipo: | DEC 48.340 2021 |
| :--- | :--- |
| **Ementa:** | Altera o Decreto nº 48.589, de 22 de março de 2023, que regulamenta o Imposto sobre Operações relativas à Circulação de Mercadorias e sobre Prestações de Serviços de Transporte Interestadual e Intermunicipal e de Comunicação – ICMS. |
| **Indexação:** | Thesaurus/Tema/[...]/ICMS<br>Thesaurus/Tema/[...]/Substituição Tributária |
| **Resumo:** | # |

==================================================================================

Público-alvo: Os bibliotecários da Assembleia Legislativa do Estado de Minas Gerais, que vão indexar os documentos legislativos, atribuindo indexação e resumo.

---
Histórico da Conversa:
{historico_da_conversa}
---
Documento:
{conteudo_do_documento}
---
Pergunta: {pergunta_usuario}
""",

    "Regimento Interno da ALMG": """
Personalização da IA:
Você é um assistente especializado no Regimento Interno da Assembleia Legislativa de Minas Gerais.
Sua única fonte de informação é o documento "Regimento Interno da ALMG.pdf".

====================================================================

Regras de Resposta:
- Responda de forma objetiva, formal e clara.
- Se a informação não estiver no documento, responda: "A informação não foi encontrada no documento."
- Para cada resposta, forneça uma explicação detalhada, destrinchando o processo e as regras relacionadas. Sempre que possível, cite os artigos, parágrafos e incisos relevantes do Regimento.
- Sempre cite a fonte da sua resposta. A fonte deve ser a página onde a informação foi encontrada no documento, no seguinte formato: "Você pode verificar a informação na página [cite a página] do Regimento Interno da ALMG."

---
Histórico da Conversa:
{historico_da_conversa}
---
Documento:
{conteudo_do_documento}
---
Pergunta: {pergunta_usuario}
""",

    "Constituição Estadual": """
Personalização da IA:
Você é um assistente especializado na Constituição do Estado de Minas Gerais.
Sua única fonte de informação é o documento "Constituição Estadual.pdf".

====================================================================

Regras de Resposta:
- Responda de forma objetiva, formal e clara.
- Se a informação não estiver no documento, responda: "A informação não foi encontrada no documento."
- Para cada resposta, forneça uma explicação detalhada, destrinchando o processo e as regras relacionadas. Sempre que possível, cite os artigos, parágrafos e incisos relevantes da Constituição.
- Sempre cite a fonte da sua resposta. A fonte deve ser a página onde a informação foi encontrada no documento, no seguinte formato: "Você pode verificar a informação na página [cite a página] da Constituição Estadual."

---
Histórico da Conversa:
{historico_da_conversa}
---
Documento:
{conteudo_do_documento}
---
Pergunta: {pergunta_usuario}
""",

    "Manual de redação parlamentar": """
Personalização da IA:
Você é um assistente especializado no Manual de Redação Parlamentar da Assembleia Legislativa de Minas Gerais.
Sua única fonte de informação é o documento "manual_redacao.pdf".

====================================================================

Regras de Resposta:
- Responda de forma objetiva, formal e clara.
- Se a informação não estiver no documento, responda: "A informação não foi encontrada no documento."
- Para cada resposta, forneça uma explicação detalhada, destrinchando o processo e as regras relacionadas. Sempre que possível, cite as seções, capítulos e exemplos relevantes do Manual de Redação.
- Sempre cite a fonte da sua resposta. A fonte deve ser a página onde a informação foi encontrada no documento, no seguinte formato: "Você pode verificar a informação na página [cite a página] do Manual de redação parlamentar."

---
Histórico da Conversa:
{historico_da_conversa}
---
Documento:
{conteudo_do_documento}
---
Pergunta: {pergunta_usuario}
""",
}

def carregar_documento_do_disco(caminho_arquivo):
    if not os.path.exists(caminho_arquivo):
        st.error(f"Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
        return None

    extensao = os.path.splitext(caminho_arquivo)[1].lower()

    try:
        if extensao == ".txt":
            with open(caminho_arquivo, 'r', encoding='utf-8') as f:
                return f.read()
        elif extensao == ".docx":
            doc = docx.Document(caminho_arquivo)
            texto = [paragrafo.text for paragrafo in doc.paragraphs]
            return "\n".join(texto)
        elif extensao == ".pdf":
            texto = ""
            with fitz.open(caminho_arquivo) as pdf_doc:
                for page in pdf_doc:
                    texto += page.get_text()
            return texto
        else:
            st.error(f"Erro: Formato de arquivo '{extensao}' não suportado.")
            return None
    except Exception as e:
        st.error(f"Ocorreu um erro ao ler o arquivo: {e}")
        return None

def get_api_key():
    api_key = os.environ.get("GOOGLE_API_KEY") or st.secrets.get("GOOGLE_API_KEY")
    if not api_key:
        st.error("Erro: A chave de API não foi configurada.")
        return None
    return api_key

def answer_from_document(prompt_completo, api_key):
    if not api_key:
        return "Erro: Chave de API ausente."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

    payload = {
        "contents": [{"parts": [{"text": prompt_completo}]}]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        resposta = result.get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "Não foi possível gerar a resposta.")
        return resposta
    except requests.exceptions.HTTPError as http_err:
        return f"Erro na comunicação com a API: {http_err}"
    except Exception as e:
        return f"Ocorreu um erro: {e}"

# --- Funções para Gerador de Termos e Resumos ---
def carregar_dicionario_termos(nome_arquivo):
    termos = []
    mapa_hierarquia = {}
    
    try:
        with open(nome_arquivo, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                partes = [p.strip() for p in line.split('>') if p.strip()]
                
                if not partes:
                    continue

                termo_especifico = partes[-1]
                if termo_especifico:
                    termo_especifico = termo_especifico.replace('\t', '')
                    termos.append(termo_especifico)
                
                if len(partes) > 1:
                    termo_pai = partes[-2].replace('\t', '')
                    if termo_pai not in mapa_hierarquia:
                        mapa_hierarquia[termo_pai] = []
                    mapa_hierarquia[termo_pai].append(termo_especifico)
                    
    except FileNotFoundError:
        st.error(f"Erro: O arquivo '{nome_arquivo}' não foi encontrado.")
        return [], {}
    except Exception as e:
        st.error(f"Ocorreu um erro ao carregar o dicionário de termos: {e}")
        return [], {}
        
    return termos, mapa_hierarquia

def aplicar_logica_hierarquia(termos_sugeridos, mapa_hierarquia):
    termos_finais = set(termos_sugeridos)
    mapa_inverso_hierarquia = {}
    
    for pai, filhos in mapa_hierarquia.items():
        for filho in filhos:
            mapa_inverso_hierarquia[filho] = pai
    
    termos_a_remover = set()
    for termo in termos_sugeridos:
        if termo in mapa_inverso_hierarquia:
            termo_pai = mapa_inverso_hierarquia[termo]
            if termo_pai in termos_finais:
                termos_a_remover.add(termo_pai)
                
    termos_finais = termos_finais - termos_a_remover
    return list(termos_finais)

def gerar_resumo(texto_original):
    api_key = get_api_key()
    
    if not api_key:
        st.error("Erro: A chave de API não foi configurada.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    regras_adicionais = """
    - Mantenha o resumo em um único parágrafo, com no máximo 4 frases.
    - Use linguagem formal e evite gírias.
    - Mantenha um tom objetivo e neutro.
    - Use verbos na terceira pessoa do singular, na voz ativa.
    - Para descrever ações ou responsabilidades de autoridades, prefira o uso de verbos auxiliares como 'deve' ou 'pode' para indicar obrigação ou possibilidade.
    - Evite o uso de verbos com partícula apassivadora ou de indeterminação do sujeito.
    - Evite iniciar frases com 'Esta política', 'A lei' ou termos semelhantes.
    - Separe as siglas com o caractere "–".
    - Inicie o resumo diretamente com um verbo na terceira pessoa do singular, sem sujeito explícito.
    - Não inclua a parte sobre a vigência da lei.
    - O resumo deve focar em três pontos principais:
        1. O que o programa institui e a quem se destina.
        2. Quem aciona o alerta e em que condições.
        3. Quais informações podem ser incluídas nas mensagens e quais tecnologias são permitidas.
    - O resumo não deve mencionar:
        - Detalhes sobre a Lei Geral de Proteção de Dados – LGPD.
        - Detalhes específicos sobre a Defesa Civil, ANATEL ou outros órgãos.
        - Nomes específicos de programas.
        - 'Minas Gerais' ou 'Estado de Minas Gerais'.
    - Todas as palavras de origem estrangeira devem ser escritas entre aspas.
    - Represente os numerais de 0 a 9 por extenso, para 10 ou mais, use apenas o algarismo.
    """

    prompt_resumo = f"""
    Resuma a seguinte proposição legislativa de forma clara, concisa e com as regras abaixo.
    
    Regras para o Resumo:
    {regras_adicionais}
    
    Texto da Proposição: {texto_original}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt_resumo}]}],
        "tools": [{"google_search": {}}]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        return result.get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "")
    except requests.exceptions.HTTPError as http_err:
        st.error(f"Erro na comunicação com a API: {http_err}")
    except Exception as e:
        st.error(f"Ocorreu um erro: {e}")
        
    return "Não foi possível gerar o resumo."

def gerar_termos_llm(texto_original, termos_dicionario, num_termos):
    api_key = get_api_key()
    
    if not api_key:
        st.error("Erro: A chave de API não foi configurada.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    prompt_termos = f"""
    A partir do texto abaixo, selecione até {num_termos} termos de indexação relevantes.
    Os termos de indexação devem ser selecionados EXCLUSIVAMENTE da seguinte lista:
    {', '.join(termos_dicionario)}
    Se nenhum termo da lista for aplicável, a resposta deve ser uma lista JSON vazia: [].
    A resposta DEVE ser uma lista JSON de strings, sem texto adicional antes ou depois.
    
    Texto da Proposição: {texto_original}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt_termos}]}],
        "tools": [{"google_search": {}}]
    }

    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
        
        json_string = result.get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "")
        
        termos_sugeridos = []
        matches = re.findall(r'(\[.*?\])', json_string, re.DOTALL)
        
        for match in matches:
            cleaned_string = match.replace("'", '"')
            try:
                parsed_list = json.loads(cleaned_string)
                if isinstance(parsed_list, list) and all(isinstance(item, str) for item in parsed_list):
                    termos_sugeridos = parsed_list
                    break
            except json.JSONDecodeError:
                continue
        
        return termos_sugeridos
        
    except requests.exceptions.HTTPError as http_err:
        st.error(f"Erro na comunicação com a API: {http_err}")
    except Exception as e:
        st.error(f"Ocorreu um erro: {e}")
        
    return []

# --- Funções para Conversor de PDF em Texto (OCR) ---
def correct_ocr_text(raw_text):
    """
    Chama a API da Gemini para corrigir erros de OCR, normalizar a ortografia arcaica,
    remover cabeçalho e formatar dados estruturados como tabela em Markdown — SEM negrito.
    """
    api_key = get_api_key()
    if not api_key:
        st.error("Chave de API do Gemini não encontrada. Verifique as variáveis de ambiente ou secrets.")
        return raw_text
    apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    system_prompt = """
Você é um corretor ortográfico e normalizador de texto brasileiro, especializado em documentos históricos.
Sua tarefa é receber um texto bruto de OCR, corrigir erros e normalizar a ortografia arcaica (ex: 'Geraes' → 'Gerais', 'legaes' → 'legais').
**Você deve retornar o resultado INTEIRO no formato Markdown.**

Regras estritas:
- **NÃO use negrito (`**` ou `__`) em NENHUMA parte do texto.**
- **Remova o cabeçalho do jornal/documento**: TÍTULO (ex: "MINAS GERAES"), data, número da edição, assinatura, venda avulsa, linhas divisórias. Mantenha apenas o corpo do texto.
- **Corrija erros óbvios de OCR** e normalize ortografia arcaica.
- **Se o texto contiver pares claros de "rótulo … valor" (ex: "Ativo … 450:200$000"), recrie-os como uma tabela Markdown com DUAS COLUNAS, SEM CABEÇALHOS.**
  - A primeira coluna deve conter o item descritivo (ex: "Saldo de 1930", "Rendas arrecadadas").
  - A segunda coluna deve conter o valor correspondente (ex: "13:868$112", "243:234$308").
  - **Não crie cabeçalhos como "Item" e "Valor". Deixe as células vazias na primeira linha ou use apenas `--- | ---` como separador.**
  - **Se houver títulos seccionais (ex: "Receita:", "Despesa:", "Situação patrimonial..."), inclua-os como linhas de tabela, com o texto na primeira coluna e a segunda coluna vazia.**
  - **Mantenha a ordem exata dos itens do texto original. Não invente, não resuma, não omita.**
  - **Nunca adicione linhas como "Total", "Subtotal", "Geral", etc., a menos que estejam explicitamente no texto.**
- **Retorne APENAS o texto corrigido em Markdown**, sem explicações, sem blocos de código (ex: ```markdown```), sem introduções.
"""
    payload = {
        "contents": [{"parts": [{"text": raw_text}]}],
        "system_instruction": {"parts": [{"text": system_prompt}]}, 
    }
    try:
        response = requests.post(apiUrl, 
                                headers={'Content-Type': 'application/json'}, 
                                data=json.dumps(payload))
        if response.status_code == 400:
            st.error(f"Erro detalhado da API (400): {response.text}. Verifique o tamanho do PDF.")
            return raw_text
        response.raise_for_status() 
        result = response.json()
        corrected_text = result.get("candidates", [])[0].get("content", {}).get("parts", [])[0].get("text", "")
        return corrected_text if corrected_text else raw_text
    except requests.exceptions.HTTPError as http_err:
        st.error(f"Erro HTTP ({http_err.response.status_code}) na correção via Gemini. Exibindo texto bruto.")
    except Exception as e:
        st.error(f"Ocorreu um erro inesperado durante a correção via Gemini: {e}. Exibindo texto bruto.")
    return raw_text
# --- Função Principal da Aplicação ---
def run_app():
    st.set_page_config(page_title="Assistente Virtual da GIL")
    
    st.markdown("""
        <style>
        .title-container {
            text-align: center;
            background-color: #f0f0f0;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .main-title {
            color: #d11a2a;
            font-size: 3em;
            font-weight: bold;
            margin-bottom: 0;
        }
        .subtitle-gil {
            color: gray;
            font-size: 1.5em;
            margin-top: 5px;
        }
        .stRadio > div {
            flex-direction: column;
            align-items: flex-start;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <div class="title-container">
            <h1 class="main-title">Assistente Virtual da GIL</h1>
            <h4 class="subtitle-gil">Gerência de Informação Legislativa – GIL/GDI</h4>
        </div>
    """, unsafe_allow_html=True)

    st.divider()
    opcao = st.radio(
        "Escolha a funcionalidade:",
        (
            "Extrator de Diários Oficiais",
            "Gerador de Links do Jornal Minas Gerais",
            "Chatbot – Gerência de Informação Legislativa",
            "Gerador de Termos e Resumos de Proposições",
            "Conversor de PDF em texto (OCR)"
        ),
        horizontal=False
    )
    st.divider()

    if opcao == "Extrator de Diários Oficiais":
        diario_escolhido = st.radio(
            "Selecione o tipo de Diário para extração:",
            ('Legislativo', 'Administrativo', 'Executivo'),
            horizontal=True
        )
        st.divider()

        pdf_bytes = None
        if diario_escolhido == 'Executivo':
            modo = "Upload de arquivo"
            st.info("Para o Diário do Executivo, é necessário fazer o upload do arquivo.")
        else:
            modo = st.radio(
                "Como deseja fornecer o PDF?",
                ("Upload de arquivo", "Link da internet"),
                horizontal=True
            )

        if modo == "Upload de arquivo":
            uploaded_file = st.file_uploader(
                f"Faça o upload do arquivo PDF do **Diário {diario_escolhido}**.",
                type="pdf"
            )
            if uploaded_file is not None:
                pdf_bytes = uploaded_file.read()
        else:
            url = st.text_input("Cole o link do PDF aqui:")
            if url:
                try:
                    with st.spinner("Baixando PDF..."):
                        resp = requests.get(url, timeout=30)
                        if resp.status_code == 200:
                            ctype = resp.headers.get("Content-Type", "")
                            if ("pdf" not in ctype.lower()) and (not url.lower().endswith(".pdf")):
                                st.warning("O link não parece apontar para um PDF (Content-Type != PDF). Tentarei processar mesmo assim.")
                            pdf_bytes = resp.content
                        else:
                            st.error(f"Falha ao baixar (status {resp.status_code}).")
                except Exception as e:
                    st.error(f"Erro ao baixar o PDF: {e}")

        if pdf_bytes:
            try:
                if diario_escolhido == 'Legislativo':
                    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
                    text = ""
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text + "\n"
                    text = re.sub(r"[ \t]+", " ", text)
                    text = re.sub(r"\n+", "\n", text)
                    
                    with st.spinner('Extraindo dados do Diário do Legislativo...'):
                        processor = LegislativeProcessor(text)
                        extracted_data = processor.process_all()

                        output = io.BytesIO()
                        excel_file_name = "Legislativo_Extraido.xlsx"
                        with pd.ExcelWriter(output, engine="openpyxl") as writer:
                            for sheet_name, df in extracted_data.items():
                                df.to_excel(writer, sheet_name=sheet_name, index=False)
                        output.seek(0)
                        download_data = output
                        file_name = excel_file_name
                        mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

                elif diario_escolhido == 'Administrativo':
                    with st.spinner('Extraindo dados do Diário Administrativo...'):
                        processor = AdministrativeProcessor(pdf_bytes)
                        csv_data = processor.to_csv()
                        if csv_data:
                            download_data = csv_data
                            file_name = "Administrativo_Extraido.csv"
                            mime_type = "text/csv"
                        else:
                            download_data = None
                            file_name = None
                            mime_type = None
                else:
                    with st.spinner('Extraindo dados do Diário do Executivo...'):
                        processor = ExecutiveProcessor(pdf_bytes)
                        csv_data = processor.to_csv()
                        if csv_data:
                            download_data = csv_data
                            file_name = "Executivo_Extraido.csv"
                            mime_type = "text/csv"
                        else:
                            download_data = None
                            file_name = None
                            mime_type = None

                if download_data:
                    st.success("Dados extraídos com sucesso! ✅")
                    st.divider()
                    st.download_button(
                        label="Clique aqui para baixar o arquivo",
                        data=download_data,
                        file_name=file_name,
                        mime=mime_type
                    )
                    st.info(f"O download do arquivo **{file_name}** está pronto.")

            except Exception as e:
                st.error(f"Ocorreu um erro ao processar o arquivo: {e}")

    elif opcao == "Gerador de Links do Jornal Minas Gerais":
        min_data = date(1835, 1, 1)
        max_data = datetime.today().date()

        if "data" not in st.session_state:
            data_inicial = datetime.today().date()
            if data_inicial < min_data:
                data_inicial = min_data
            elif data_inicial > max_data:
                data_inicial = max_data
            st.session_state.data = data_inicial

        data_selecionada = st.date_input(
            "Selecione a data de publicação:",
            st.session_state.data,
            min_value=min_data,
            max_value=max_data
        )
        st.session_state.data = data_selecionada

        col1, col2, col3 = st.columns([1,1,1])

        with col1:
            if st.session_state.data > min_data:
                if st.button("⬅️ Dia Anterior"):
                    dia_anterior()
            else:
                st.button("⬅️ Dia Anterior", disabled=True)

        with col2:
            if st.button("📅 Hoje"):
                ir_hoje()

        with col3:
            if st.session_state.data < max_data:
                if st.button("➡️ Próximo Dia"):
                    dia_posterior()
            else:
                st.button("➡️ Próximo Dia", disabled=True)

        if st.button("📝 Gerar link"):
            data_formatada_link = st.session_state.data.strftime("%Y-%m-%d")
            dados_dict = {"dataPublicacaoSelecionada": f"{data_formatada_link}T06:00:00.000Z"}
            json_str = json.dumps(dados_dict, separators=(',', ':'))
            novo_dados = json_str.replace("{", "%7B").replace("}", "%7D").replace('"', "%22")
            novo_link = f"https://www.jornalminasgerais.mg.gov.br/edicao-do-dia?dados={novo_dados}"
            st.markdown(f"**Data escolhida:** {st.session_state.data.strftime('%d/%m/%Y')}")
            st.success("Link gerado com sucesso!")
            st.text_area("Link:", value=novo_link, height=100)

    elif opcao == "Chatbot – Gerência de Informação Legislativa":
        file_names = list(DOCUMENTOS_PRE_CARREGADOS.keys())
        if not file_names:
            st.warning("Nenhum documento pré-carregado. Por favor, adicione arquivos à lista `DOCUMENTOS_PRE_CARREGADOS` no código.")
        else:
            selected_file_name_display = st.selectbox("Escolha o assunto sobre o qual você quer conversar:", file_names)
            selected_file_path = DOCUMENTOS_PRE_CARREGADOS[selected_file_name_display]
            
            if selected_file_name_display in PROMPTS_POR_DOCUMENTO:
                prompt_base = PROMPTS_POR_DOCUMENTO[selected_file_name_display]
            else:
                st.error("Erro: Não foi encontrado um prompt personalizado para este documento.")
                prompt_base = "Responda a pergunta do usuário com base no seguinte documento: {conteudo_do_documento}. Pergunta: {pergunta_usuario}"
            
            DOCUMENTO_CONTEUDO = carregar_documento_do_disco(selected_file_path)

            if DOCUMENTO_CONTEUDO:
                st.success(f"Documento '{selected_file_name_display}' carregado com sucesso!")
                
                if "messages" not in st.session_state:
                    st.session_state.messages = []

                for message in st.session_state.messages:
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])

                if pergunta_usuario := st.chat_input("Faça sua pergunta:"):
                    st.session_state.messages.append({"role": "user", "content": pergunta_usuario})
                    
                    with st.chat_message("user"):
                        st.markdown(pergunta_usuario)

                    with st.chat_message("assistant"):
                        with st.spinner("Buscando a resposta..."):
                            api_key = get_api_key()
                            if api_key and DOCUMENTO_CONTEUDO:
                                prompt_completo = prompt_base.format(
                                    historico_da_conversa=st.session_state.messages,
                                    conteudo_do_documento=DOCUMENTO_CONTEUDO,
                                    pergunta_usuario=pergunta_usuario
                                )
                                resposta = answer_from_document(prompt_completo, api_key)
                                st.markdown(resposta)
                                st.session_state.messages.append({"role": "assistant", "content": resposta})

            if st.button("Limpar Chat"):
                st.session_state.messages = []
                st.rerun()

    elif opcao == "Gerador de Termos e Resumos de Proposições":
        TIPOS_DOCUMENTO = {
            "Documentos Gerais": "dicionario_termos.txt"
        }

        tipo_documento_selecionado = st.selectbox(
            "Selecione o tipo de documento:",
            options=["Proposição", "Requerimento"],
        )

        num_termos_selecionado = st.selectbox(
            "Selecione a quantidade de termos de indexação:",
            options=["Até 3", "de 3 a 5", "5+"],
        )

        num_termos = 10
        if num_termos_selecionado == "Até 3":
            num_termos = 3
        elif num_termos_selecionado == "de 3 a 5":
            num_termos = 5

        arquivo_dicionario = TIPOS_DOCUMENTO["Documentos Gerais"]
        termo_dicionario, mapa_hierarquia = carregar_dicionario_termos(arquivo_dicionario)

        if "Minas Gerais (MG)" in termo_dicionario:
            termo_dicionario.remove("Minas Gerais (MG)")

        texto_proposicao = st.text_area(
            "Cole o texto da proposição aqui:", 
            height=300,
            placeholder="Ex: 'A presente proposição dispõe sobre a criação de um programa de incentivo...'"
        )

        if st.button("Gerar Resumo e Termos"):
            if not texto_proposicao:
                st.warning("Por favor, cole o texto da proposição para continuar.")
            else:
                with st.spinner('Gerando resumo e termos...'):
                    resumo_gerado = ""
                    termos_finais = []
                    
                    match_doacao = re.search(r"Município de ([\w\s-]+?)(?:\s+o\simóvel|\s+os\simóveis|\s*\d)", texto_proposicao, re.IGNORECASE)
                    match_servidao = re.search(r"declara de utilidade pública,.*servidão.*no Município de ([\w\s-]+)", texto_proposicao, re.IGNORECASE | re.DOTALL)
                    match_utilidade_publica = re.search(r"declara de utilidade pública.*no Município de ([\w\s-]+)", texto_proposicao, re.IGNORECASE | re.DOTALL)
                    
                    if match_doacao:
                        municipio = match_doacao.group(1).strip()
                        termos_finais = ["Doação de Imóvel", municipio]
                        resumo_gerado = "Não precisa de resumo."
                    elif match_servidao:
                        municipio = match_servidao.group(1).strip()
                        termos_finais = ["Servidão Administrativa", municipio]
                        resumo_gerado = "Não precisa de resumo."
                    elif match_utilidade_publica:
                        municipio = match_utilidade_publica.group(1).strip()
                        termos_finais = ["Utilidade Pública", municipio]
                        resumo_gerado = "Não precisa de resumo."
                    else:
                        if tipo_documento_selecionado == "Proposição":
                            resumo_gerado = gerar_resumo(texto_proposicao)
                        elif tipo_documento_selecionado == "Requerimento":
                            resumo_gerado = "Não precisa de resumo."

                        termos_sugeridos_brutos = gerar_termos_llm(texto_proposicao, termo_dicionario, num_termos)
                        
                        if re.search(r"institui (?:a|o) (?:política|programa) estadual|cria (?:a|o) (?:política|programa) estadual", texto_proposicao, re.IGNORECASE):
                            if termos_sugeridos_brutos is not None and "Política Pública" not in termos_sugeridos_brutos:
                                termos_sugeridos_brutos.append("Política Pública")

                        if termos_sugeridos_brutos is not None:
                            termos_finais = aplicar_logica_hierarquia(termos_sugeridos_brutos, mapa_hierarquia)
                        else:
                            termos_finais = []

                    st.subheader("Resumo")
                    st.markdown(f"<p style='text-align: justify;'>{resumo_gerado}</p>", unsafe_allow_html=True)
                    
                    st.subheader("Termos de Indexação")
                    if termos_finais:
                        termos_str = ", ".join(termos_finais)
                        st.success(termos_str)
                    else:
                        st.warning("Nenhum termo relevante foi encontrado no dicionário.")

    elif opcao == "Conversor de PDF em texto (OCR)":
        OCRMypdf_PATH = shutil.which("ocrmypdf")
        PANDOC_PATH = shutil.which("pandoc") 

        if not OCRMypdf_PATH or not PANDOC_PATH:
            st.error("""
                O executável **'ocrmypdf' ou 'pandoc' não foi encontrado**.
                Verifique se o arquivo `packages.txt` (na raiz do repositório) contém as linhas `ocrmypdf` e `pandoc`.
                Pode ser necessário forçar um re-deploy ou restart do aplicativo.
            """)
            st.stop()

        st.title("Conversor de PDF para ODT (LibreOffice)")
        st.warning("⚠️ **AVISO IMPORTANTE:** Este aplicativo só deve ser utilizado para edições antigas do Jornal Minas Gerais. Versões atuais são pesadas e podem fazer o aplicativo parar de funcionar devido aos limites de recursos.")

        uploaded_file = st.file_uploader("Escolha um arquivo PDF...", type=["pdf"])

        if uploaded_file is not None:
            st.info("Arquivo carregado com sucesso. Processando...")
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as input_file:
                input_file.write(uploaded_file.read())
                input_filepath = input_file.name

            output_ocr_filepath = os.path.join(tempfile.gettempdir(), "output_ocr.pdf")
            markdown_filepath = os.path.join(tempfile.gettempdir(), "texto_temporario.md") 
            odt_filepath = os.path.join(tempfile.gettempdir(), "documento_final.odt") 

            try:
                with st.spinner("1/3: Extraindo texto bruto do PDF com OCR..."):
                    command_ocr = [
                        OCRMypdf_PATH,
                        "--force-ocr",
                        "--sidecar",
                        markdown_filepath, 
                        input_filepath,
                        output_ocr_filepath
                    ]
                    
                    subprocess.run(command_ocr, check=True, capture_output=True, text=True)
                    st.success("Extração de texto concluída.")

                if os.path.exists(markdown_filepath):
                    with open(markdown_filepath, "r") as f:
                        sidecar_text_raw = f.read()
                    
                    with st.spinner("2/3: Corrigindo ortografia arcaica, removendo cabeçalhos e formatando tabelas via IA..."):
                        sidecar_text_corrected = correct_ocr_text(sidecar_text_raw)
                    
                    with open(markdown_filepath, "w", encoding='utf-8') as f:
                        f.write(sidecar_text_corrected)

                    with st.spinner("3/3: Convertendo Markdown para arquivo ODT do LibreOffice..."):
                        command_pandoc = [
                            PANDOC_PATH,
                            "--standalone", 
                            "-s",
                            markdown_filepath,
                            "-o",
                            odt_filepath
                        ]
                        subprocess.run(command_pandoc, check=True, capture_output=True, text=True)
                        st.success("Conversão para ODT concluída! Seu documento está pronto para download.")

                    st.markdown("---")
                    st.subheader("✅ Processo Finalizado com Sucesso")
                    st.info("O download abaixo contém o texto corrigido, com ortografia normalizada e tabelas reestruturadas, pronto para edição no LibreOffice Writer.")
                    
                    with open(odt_filepath, "rb") as f:
                        st.download_button(
                            label="⬇️ Baixar Documento Formatado (.odt)",
                            data=f.read(),
                            file_name="documento_final_formatado.odt",
                            mime="application/vnd.oasis.opendocument.text"
                        )
                    
                    st.markdown("---")

            except subprocess.CalledProcessError as e:
                st.error(f"Erro ao processar o arquivo (OCR ou Pandoc). Detalhes: {e.stderr}")
                st.code(f"Comando tentado: {' '.join(e.cmd)}")
            except Exception as e:
                st.error(f"Ocorreu um erro inesperado: {e}")
            finally:
                for filepath in [input_filepath, output_ocr_filepath, markdown_filepath, odt_filepath]:
                    if os.path.exists(filepath):
                        try:
                            os.unlink(filepath)
                        except Exception:
                            pass

if __name__ == "__main__":
    run_app()
