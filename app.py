# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime
import re, zipfile, unicodedata
from io import BytesIO

import pandas as pd
import streamlit as st
import pdfplumber
from pypdf import PdfReader, PdfWriter
from rapidfuzz import fuzz


st.set_page_config(page_title="Conciliador NF x Comprovante", layout="wide")

BASE_DIR = Path(__file__).parent
DIR_NFS = BASE_DIR / "entrada" / "nfs"
DIR_COMP = BASE_DIR / "entrada" / "comprovantes"
DIR_TMP = BASE_DIR / "temporario"
DIR_REL = BASE_DIR / "relatorios"

for p in [DIR_NFS, DIR_COMP, DIR_TMP, DIR_REL]:
    p.mkdir(parents=True, exist_ok=True)


# =========================
# NORMALIZAÇÕES
# =========================
STOPWORDS = {
    "LTDA", "EIRELI", "ME", "EPP", "S A", "SA", "S/A", "SERVICOS", "SERVIÇOS",
    "ADMINISTRATIVOS", "ADMINISTRATIVA", "ADMINISTRATIVO", "COMERCIO", "COMÉRCIO",
    "INDUSTRIA", "INDÚSTRIA", "REPRESENTACOES", "REPRESENTAÇÕES", "PRODUTOS",
    "MEDICOS", "MÉDICOS", "HOSPITALARES", "CONSULTORIA", "EMPRESARIAL"
}


def limpar_texto(txt):
    return re.sub(r"\s+", " ", txt or "").strip()


def remover_acentos(txt):
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    return "".join(c for c in txt if not unicodedata.combining(c))


def normalizar_nome(txt):
    txt = remover_acentos(str(txt or "")).upper()
    txt = re.sub(r"[^A-Z0-9 ]", " ", txt)
    partes = [p for p in txt.split() if p not in STOPWORDS and len(p) > 1]
    return " ".join(partes)


def somente_numeros(txt):
    return re.sub(r"\D", "", str(txt or ""))


def normalizar_valor(txt):
    if txt is None:
        return None
    txt = str(txt).replace("R$", "").strip()
    txt = txt.replace(".", "").replace(",", ".")
    try:
        return round(float(txt), 2)
    except Exception:
        return None


def formatar_valor(v):
    if v is None or v == "":
        return ""
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def limpar_nome_arquivo(txt):
    txt = normalizar_nome(txt) or "SEM_NOME"
    txt = re.sub(r"[^A-Z0-9_ -]", "", txt)
    txt = re.sub(r"\s+", "_", txt).strip("_")
    return txt[:90]


# =========================
# EXTRAÇÃO DE NFs
# =========================
def ler_pdf_texto(caminho):
    texto = ""
    with pdfplumber.open(caminho) as pdf:
        for pag in pdf.pages:
            texto += "\n" + (pag.extract_text() or "")
    return limpar_texto(texto)


def extrair_cnpjs(texto):
    return re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", texto or "")


def extrair_cpfs(texto):
    return re.findall(r"\d{3}\.\d{3}\.\d{3}-\d{2}", texto or "")


def extrair_numero_nf(texto):
    padroes = [
        r"N[uú]mero da Nota\s*(\d+)",
        r"N[ºo°\. ]+\s*(\d{4,})",
        r"RPS\s*N[ºo°\. ]*\s*(\d+)",
        r"NFS-e\s*(\d{4,})",
    ]
    for p in padroes:
        m = re.search(p, texto, re.I)
        if m:
            return m.group(1).lstrip("0") or m.group(1)
    return ""


def extrair_fornecedor_nf(texto):
    # Padrão NFS-e SP: Nome/Razão Social antes do endereço
    ms = re.findall(r"Nome/Raz[aã]o Social:\s*(.*?)\s*Endere[cç]o:", texto, re.I)
    if ms:
        return limpar_texto(ms[0])

    cnpjs = extrair_cnpjs(texto)
    if cnpjs:
        pos = texto.find(cnpjs[0])
        trecho = texto[pos:pos+250]
        m = re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\s+[\d\.\-]*\s*(.*?)\s+(?:AV|RUA|AL|TRAV|ESTR|ROD)\s", trecho, re.I)
        if m:
            return limpar_texto(m.group(1))

    return ""


def extrair_valor_nf(texto):
    padroes = [
        r"VALOR TOTAL DO SERVI[ÇC]O\s*=\s*R\$\s*([\d\.\,]+)",
        r"Valor Total da Nota\s*R?\$?\s*([\d\.\,]+)",
        r"Valor Total.*?R\$\s*([\d\.\,]+)",
        r"TOTAL\s*R\$\s*([\d\.\,]+)",
    ]
    for p in padroes:
        m = re.search(p, texto, re.I)
        if m:
            return normalizar_valor(m.group(1))
    return None


def ler_nf(caminho):
    texto = ler_pdf_texto(caminho)
    cnpjs = extrair_cnpjs(texto)
    return {
        "arquivo": str(caminho),
        "texto": texto,
        "numero_nf": extrair_numero_nf(texto),
        "fornecedor": extrair_fornecedor_nf(texto),
        "fornecedor_norm": normalizar_nome(extrair_fornecedor_nf(texto)),
        "cnpj": cnpjs[0] if cnpjs else "",
        "valor": extrair_valor_nf(texto),
        "tokens_nome": set(normalizar_nome(extrair_fornecedor_nf(texto)).split()),
    }


# =========================
# EXTRAÇÃO COMPROVANTES
# =========================
def salvar_pagina_pdf(pdf_origem, i, pasta):
    pasta.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(pdf_origem))
    writer = PdfWriter()
    writer.add_page(reader.pages[i])
    saida = pasta / f"{Path(pdf_origem).stem}_pagina_{i+1:03d}.pdf"
    with open(saida, "wb") as f:
        writer.write(f)
    return str(saida)


def extrair_valor_comp(texto):
    padroes = [
        r"VALOR COBRADO\s*([\d\.\,]+)",
        r"VALOR DO DOCUMENTO\s*([\d\.\,]+)",
        r"VALOR:\s*R?\$?\s*([\d\.\,]+)",
        r"VALOR\s*([\d\.\,]+)",
    ]
    for p in padroes:
        m = re.search(p, texto, re.I)
        if m:
            return normalizar_valor(m.group(1))
    return None


def extrair_beneficiario(texto):
    padroes = [
        r"PAGO PARA:\s*(.*?)\s*(?:CNPJ|CPF|CHAVE PIX|INSTITUICAO|INSTITUIÇÃO)",
        r"BENEFICIARIO:\s*(.*?)\s*(?:CPF/CNPJ|CNPJ|NOME FANTASIA|BANCO|BENEFICIARIO FINAL)",
        r"BENEFICIÁRIO:\s*(.*?)\s*(?:CPF/CNPJ|CNPJ|NOME FANTASIA|BANCO|BENEFICIÁRIO FINAL)",
    ]
    for p in padroes:
        m = re.search(p, texto, re.I)
        if m:
            return limpar_texto(m.group(1))
    return ""


def extrair_doc_comp(texto):
    docs = extrair_cnpjs(texto) + extrair_cpfs(texto)
    docs_filtrados = []
    for d in docs:
        dn = somente_numeros(d)
        if dn not in {"02629588000172", "002629588000172"}:
            docs_filtrados.append(d)
    return docs_filtrados[0] if docs_filtrados else ""


def extrair_data_comp(texto):
    padroes = [
        r"DATA DO PAGAMENTO\s*(\d{2}/\d{2}/\d{4})",
        r"DATA DA TRANSFERENCIA:\s*(\d{2}/\d{2}/\d{4})",
        r"DATA:\s*(\d{2}/\d{2}/\d{4})",
    ]
    for p in padroes:
        m = re.search(p, texto, re.I)
        if m:
            return m.group(1)
    return ""


def extrair_documento_banco(texto):
    m = re.search(r"NR\.?\s*DOCUMENTO[: ]+\s*([\d\.]+)", texto, re.I)
    if m:
        return m.group(1)
    m = re.search(r"DOCUMENTO:\s*([\d\.]+)", texto, re.I)
    return m.group(1) if m else ""


def ler_comprovantes(caminho_pdf):
    comps = []
    pasta_paginas = DIR_TMP / "paginas_comprovantes"
    with pdfplumber.open(caminho_pdf) as pdf:
        for i, pag in enumerate(pdf.pages):
            texto = limpar_texto(pag.extract_text() or "")
            arq = salvar_pagina_pdf(caminho_pdf, i, pasta_paginas)
            benef = extrair_beneficiario(texto)
            comps.append({
                "arquivo_lote": str(caminho_pdf),
                "arquivo_pagina": arq,
                "pagina": i + 1,
                "texto": texto,
                "beneficiario": benef,
                "beneficiario_norm": normalizar_nome(benef),
                "tokens_nome": set(normalizar_nome(benef).split()),
                "cnpj_cpf": extrair_doc_comp(texto),
                "valor": extrair_valor_comp(texto),
                "data_pagamento": extrair_data_comp(texto),
                "documento_banco": extrair_documento_banco(texto),
            })
    return comps


# =========================
# SCORE EXPANDIDO
# =========================
def score_match(nf, comp):
    pontos = 0
    motivos = []

    nf_doc = somente_numeros(nf.get("cnpj"))
    comp_doc = somente_numeros(comp.get("cnpj_cpf"))

    # 1. Documento completo/parcial
    if nf_doc and comp_doc:
        if nf_doc == comp_doc:
            pontos += 40
            motivos.append("CNPJ/CPF igual")
        elif len(comp_doc) >= 6 and comp_doc[:3] == nf_doc[:3] and comp_doc[-2:] == nf_doc[-2:]:
            pontos += 18
            motivos.append("CNPJ parcialmente compatível")

    # 2. Valor exato ou próximo
    nv, cv = nf.get("valor"), comp.get("valor")
    if nv is not None and cv is not None:
        diff = abs(float(nv) - float(cv))
        if diff <= 0.01:
            pontos += 35
            motivos.append("Valor igual")
        elif diff <= 1.00:
            pontos += 25
            motivos.append("Valor com diferença até R$ 1")
        elif nv and diff / float(nv) <= 0.02:
            pontos += 12
            motivos.append("Valor próximo até 2%")

    # 3. Similaridade nome completa
    nome_nf = nf.get("fornecedor_norm", "")
    nome_cp = comp.get("beneficiario_norm", "")
    sim = fuzz.token_set_ratio(nome_nf, nome_cp)
    if sim >= 90:
        pontos += 25
        motivos.append(f"Nome muito parecido ({sim:.0f}%)")
    elif sim >= 80:
        pontos += 18
        motivos.append(f"Nome parecido ({sim:.0f}%)")
    elif sim >= 65:
        pontos += 10
        motivos.append(f"Nome parcialmente parecido ({sim:.0f}%)")

    # 4. Tokens importantes em comum
    comuns = nf.get("tokens_nome", set()) & comp.get("tokens_nome", set())
    tokens_relevantes = [t for t in comuns if len(t) >= 4]
    if len(tokens_relevantes) >= 2:
        pontos += 10
        motivos.append("Duas ou mais palavras-chave em comum")
    elif len(tokens_relevantes) == 1:
        pontos += 5
        motivos.append("Uma palavra-chave em comum")

    # 5. Número NF/RPS/documento dentro do texto do comprovante
    num_nf = somente_numeros(nf.get("numero_nf"))
    texto_comp = somente_numeros(comp.get("texto"))
    if num_nf and len(num_nf) >= 2 and num_nf in texto_comp:
        pontos += 10
        motivos.append("Número da NF/RPS localizado no comprovante")

    return min(pontos, 100), " | ".join(motivos), sim


def conciliar(nfs, comps, limite_auto, limite_manual):
    resultados = []
    usados = set()

    for nf in nfs:
        candidatos = []
        for comp in comps:
            if comp["arquivo_pagina"] in usados:
                continue
            sc, motivos, sim = score_match(nf, comp)
            candidatos.append((sc, motivos, sim, comp))

        candidatos.sort(key=lambda x: x[0], reverse=True)
        melhor = candidatos[0] if candidatos else (0, "", 0, None)
        sc, motivos, sim, comp = melhor

        if comp and sc >= limite_auto:
            status = "Conciliado"
            usados.add(comp["arquivo_pagina"])
        elif comp and sc >= limite_manual:
            status = "Conferir manualmente"
        else:
            status = "NF sem comprovante"

        resultados.append({
            "status": status,
            "score": sc,
            "motivos": motivos,
            "similaridade_nome": round(sim, 2),
            "nf": nf,
            "comprovante": comp,
            "top_candidatos": candidatos[:5],
        })

    return resultados, usados


def unir_pdfs(nf, comp, pasta_saida):
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    nome = f"NF_{limpar_nome_arquivo(nf.get('numero_nf'))}_{limpar_nome_arquivo(nf.get('fornecedor'))}.pdf"
    caminho = pasta_saida / nome

    writer = PdfWriter()
    for arq in [nf["arquivo"], comp["arquivo_pagina"]]:
        reader = PdfReader(str(arq))
        for page in reader.pages:
            writer.add_page(page)

    with open(caminho, "wb") as f:
        writer.write(f)

    return str(caminho)


def zipar_pasta(pasta):
    mem = BytesIO()
    pasta = Path(pasta)
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for arq in pasta.rglob("*"):
            if arq.is_file():
                z.write(arq, arq.relative_to(pasta))
    mem.seek(0)
    return mem


# =========================
# INTERFACE
# =========================
st.title("Conciliador NF x Comprovante")
st.caption("V1.3 - match expandido por nome, valor, CNPJ, palavras-chave, NF/RPS e ranking de candidatos.")

col1, col2 = st.columns(2)
with col1:
    arquivos_nf = st.file_uploader("Enviar PDFs das NFs", type=["pdf"], accept_multiple_files=True)
with col2:
    arquivos_comp = st.file_uploader("Enviar lote(s) de comprovantes", type=["pdf"], accept_multiple_files=True)

with st.expander("Configurações avançadas", expanded=True):
    c1, c2, c3 = st.columns(3)
    limite_auto = c1.slider("Score para conciliar automático", 0, 100, 72)
    limite_manual = c2.slider("Score para enviar à conferência", 0, 100, 45)
    pasta_saida_txt = c3.text_input(
        "Pasta de saída dos PDFs unidos",
        value=str(BASE_DIR / "saida" / "conciliados")
    )

if st.button("Processar conciliação", type="primary"):
    for pasta in [DIR_NFS, DIR_COMP, DIR_TMP]:
        if pasta.exists():
            for arq in pasta.rglob("*"):
                if arq.is_file():
                    arq.unlink()
        pasta.mkdir(parents=True, exist_ok=True)

    if not arquivos_nf or not arquivos_comp:
        st.error("Envie pelo menos uma NF e um lote de comprovantes.")
        st.stop()

    for arq in arquivos_nf:
        (DIR_NFS / arq.name).write_bytes(arq.read())
    for arq in arquivos_comp:
        (DIR_COMP / arq.name).write_bytes(arq.read())

    with st.spinner("Lendo NFs..."):
        nfs = [ler_nf(p) for p in DIR_NFS.glob("*.pdf")]

    with st.spinner("Lendo e separando comprovantes..."):
        comps = []
        for p in DIR_COMP.glob("*.pdf"):
            comps.extend(ler_comprovantes(p))

    with st.spinner("Calculando associação expandida..."):
        resultados, usados = conciliar(nfs, comps, limite_auto, limite_manual)

    registros = []
    pasta_saida = Path(pasta_saida_txt)
    pasta_saida.mkdir(parents=True, exist_ok=True)

    for r in resultados:
        nf, comp = r["nf"], r["comprovante"]
        pdf_final = ""

        if r["status"] == "Conciliado" and comp:
            pdf_final = unir_pdfs(nf, comp, pasta_saida)

        registros.append({
            "status": r["status"],
            "score": r["score"],
            "motivos_match": r["motivos"],
            "similaridade_nome": r["similaridade_nome"],
            "nf_numero": nf.get("numero_nf", ""),
            "nf_fornecedor": nf.get("fornecedor", ""),
            "nf_cnpj": nf.get("cnpj", ""),
            "nf_valor": nf.get("valor", ""),
            "nf_valor_formatado": formatar_valor(nf.get("valor")),
            "comprovante_beneficiario": comp.get("beneficiario", "") if comp else "",
            "comprovante_doc": comp.get("cnpj_cpf", "") if comp else "",
            "comprovante_valor": comp.get("valor", "") if comp else "",
            "comprovante_valor_formatado": formatar_valor(comp.get("valor")) if comp else "",
            "data_pagamento": comp.get("data_pagamento", "") if comp else "",
            "pagina_comprovante": comp.get("pagina", "") if comp else "",
            "arquivo_nf": nf.get("arquivo", ""),
            "arquivo_comprovante": comp.get("arquivo_pagina", "") if comp else "",
            "pdf_final": pdf_final,
        })

        # Top 5 candidatos para auditoria
        for pos, cand in enumerate(r["top_candidatos"], start=1):
            sc, motivos, sim, c = cand
            registros.append({
                "status": f"Candidato {pos}",
                "score": sc,
                "motivos_match": motivos,
                "similaridade_nome": round(sim, 2),
                "nf_numero": nf.get("numero_nf", ""),
                "nf_fornecedor": nf.get("fornecedor", ""),
                "nf_cnpj": nf.get("cnpj", ""),
                "nf_valor": nf.get("valor", ""),
                "nf_valor_formatado": formatar_valor(nf.get("valor")),
                "comprovante_beneficiario": c.get("beneficiario", ""),
                "comprovante_doc": c.get("cnpj_cpf", ""),
                "comprovante_valor": c.get("valor", ""),
                "comprovante_valor_formatado": formatar_valor(c.get("valor")),
                "data_pagamento": c.get("data_pagamento", ""),
                "pagina_comprovante": c.get("pagina", ""),
                "arquivo_nf": nf.get("arquivo", ""),
                "arquivo_comprovante": c.get("arquivo_pagina", ""),
                "pdf_final": "",
            })

    for c in comps:
        if c["arquivo_pagina"] not in usados:
            registros.append({
                "status": "Comprovante sem NF automática",
                "score": "",
                "motivos_match": "",
                "similaridade_nome": "",
                "nf_numero": "",
                "nf_fornecedor": "",
                "nf_cnpj": "",
                "nf_valor": "",
                "nf_valor_formatado": "",
                "comprovante_beneficiario": c.get("beneficiario", ""),
                "comprovante_doc": c.get("cnpj_cpf", ""),
                "comprovante_valor": c.get("valor", ""),
                "comprovante_valor_formatado": formatar_valor(c.get("valor")),
                "data_pagamento": c.get("data_pagamento", ""),
                "pagina_comprovante": c.get("pagina", ""),
                "arquivo_nf": "",
                "arquivo_comprovante": c.get("arquivo_pagina", ""),
                "pdf_final": "",
            })

    df = pd.DataFrame(registros)
    rel = DIR_REL / f"relatorio_conciliacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    with pd.ExcelWriter(rel, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Resultado")

    st.success("Processamento concluído.")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("NFs lidas", len(nfs))
    m2.metric("Comprovantes lidos", len(comps))
    m3.metric("Conciliados", len([r for r in resultados if r["status"] == "Conciliado"]))
    m4.metric("Conferir manualmente", len([r for r in resultados if r["status"] == "Conferir manualmente"]))

    st.dataframe(df, use_container_width=True)

    with open(rel, "rb") as f:
        st.download_button("Baixar relatório Excel", f, file_name=rel.name)

    zip_saida = zipar_pasta(pasta_saida)
    st.download_button(
        "Baixar PDFs conciliados em ZIP",
        data=zip_saida,
        file_name="pdfs_conciliados.zip",
        mime="application/zip"
    )
