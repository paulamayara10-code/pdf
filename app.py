# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime
import re, zipfile
import pandas as pd
import streamlit as st
import pdfplumber
from pypdf import PdfReader, PdfWriter
from rapidfuzz import fuzz

st.set_page_config(page_title="Conciliador NF x Comprovante", layout="wide")

BASE_DIR = Path(__file__).parent
DIR_TEMP = BASE_DIR / "temp_uploads"
for p in [DIR_TEMP / "nfs", DIR_TEMP / "comprovantes"]:
    p.mkdir(parents=True, exist_ok=True)

# -------------------- utilidades --------------------
def limpar_texto(txt):
    return re.sub(r"\s+", " ", txt or "").strip()

def nums(txt):
    return re.sub(r"\D", "", str(txt or ""))

def valor_float(txt):
    if txt is None: return None
    txt = str(txt).replace("R$", "").strip().replace(".", "").replace(",", ".")
    try: return round(float(txt), 2)
    except Exception: return None

def moeda(v):
    if v is None or v == "": return ""
    try: return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception: return str(v)

def safe_name(txt):
    txt = str(txt or "SEM_NOME").upper()
    txt = re.sub(r"[^A-Z0-9_\- ]", "", txt)
    txt = re.sub(r"\s+", "_", txt).strip("_")
    return txt[:90]

def salvar_uploads(files, pasta):
    for f in pasta.glob("*"):
        if f.is_file(): f.unlink()
    salvos = []
    for arq in files or []:
        destino = pasta / arq.name
        destino.write_bytes(arq.read())
        salvos.append(destino)
    return salvos

# -------------------- leitura NF --------------------
def ler_texto_pdf(path):
    texto = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texto += "\n" + (page.extract_text() or "")
    return limpar_texto(texto)

def extrair_nf(path):
    try: texto = ler_texto_pdf(path)
    except Exception as e: texto = ""; erro = str(e)
    else: erro = ""

    cnpjs = re.findall(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", texto)
    cnpj_prestador = cnpjs[0] if cnpjs else ""

    numero = ""
    for pat in [r"N[uú]mero da Nota\s*(\d+)", r"Nota\s*(\d{4,})", r"RPS Nº\s*(\d+)"]:
        m = re.search(pat, texto, re.I)
        if m: numero = m.group(1); break

    fornecedor = ""
    m = re.search(r"Nome/Raz[aã]o Social:\s*(.*?)\s*Endere[cç]o:", texto, re.I)
    if m: fornecedor = limpar_texto(m.group(1))
    if not fornecedor:
        m = re.search(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\s+[\d\.\-]+\s+(.*?)\s+(?:AV|RUA|AL|ROD|ESTRADA)\s", texto, re.I)
        if m: fornecedor = limpar_texto(m.group(1))

    valor = None
    for pat in [r"VALOR TOTAL DO SERVI[ÇC]O\s*=\s*R\$\s*([\d\.\,]+)", r"Valor Total.*?R\$\s*([\d\.\,]+)", r"VALOR.*?R\$\s*([\d\.\,]+)"]:
        m = re.search(pat, texto, re.I)
        if m: valor = valor_float(m.group(1)); break

    return {"arquivo": str(path), "numero_nf": numero, "fornecedor": fornecedor, "cnpj": cnpj_prestador, "valor": valor, "texto": texto, "erro": erro}

# -------------------- leitura comprovante --------------------
def salvar_pagina_pdf(pdf_origem, idx, pasta):
    pasta.mkdir(parents=True, exist_ok=True)
    reader, writer = PdfReader(str(pdf_origem)), PdfWriter()
    writer.add_page(reader.pages[idx])
    out = pasta / f"{Path(pdf_origem).stem}_pagina_{idx+1:03d}.pdf"
    with open(out, "wb") as f: writer.write(f)
    return str(out)

def extrair_comprovantes(path, pasta_paginas):
    comps = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            texto = limpar_texto(page.extract_text() or "")
            arq_pag = salvar_pagina_pdf(path, i, pasta_paginas)

            valor = None
            for pat in [r"VALOR COBRADO\s*([\d\.\,]+)", r"VALOR DO DOCUMENTO\s*([\d\.\,]+)", r"VALOR:\s*R?\$?\s*([\d\.\,]+)", r"VALOR\s*([\d\.\,]+)"]:
                m = re.search(pat, texto, re.I)
                if m: valor = valor_float(m.group(1)); break

            beneficiario = ""
            for pat in [r"PAGO PARA:\s*(.*?)\s*(?:CNPJ|CPF|CHAVE PIX|INSTITUICAO)", r"BENEFICIARIO:\s*(.*?)\s*(?:CPF/CNPJ|CNPJ|NOME FANTASIA|BANCO)", r"BENEFICIÁRIO:\s*(.*?)\s*(?:CPF/CNPJ|CNPJ|NOME FANTASIA|BANCO)"]:
                m = re.search(pat, texto, re.I)
                if m: beneficiario = limpar_texto(m.group(1)); break

            cands = []
            for pat in [r"CNPJ:\s*([\d\.\*/-]+)", r"CPF/CNPJ:\s*([\d\.\*/-]+)", r"CPF:\s*([\d\.\*/-]+)"]:
                cands += re.findall(pat, texto, re.I)
            cands = [c for c in cands if "02.629" not in c and "2.629" not in c and "002." not in c]
            cnpj = cands[0] if cands else ""

            data = ""
            for pat in [r"DATA DO PAGAMENTO\s*(\d{2}/\d{2}/\d{4})", r"DATA DA TRANSFERENCIA:\s*(\d{2}/\d{2}/\d{4})", r"DATA:\s*(\d{2}/\d{2}/\d{4})"]:
                m = re.search(pat, texto, re.I)
                if m: data = m.group(1); break

            comps.append({"arquivo_lote": str(path), "arquivo_pagina": arq_pag, "pagina": i+1, "beneficiario": beneficiario, "cnpj": cnpj, "valor": valor, "data_pagamento": data, "texto": texto})
    return comps

# -------------------- score melhorado --------------------
def score_match(nf, comp):
    score = 0
    motivos = []
    nf_cnpj, comp_cnpj = nums(nf.get("cnpj")), nums(comp.get("cnpj"))

    # CNPJ completo ou parcialmente mascarado
    if nf_cnpj and comp_cnpj:
        if nf_cnpj == comp_cnpj:
            score += 45; motivos.append("CNPJ igual")
        elif len(comp_cnpj) >= 5 and (comp_cnpj[:3] == nf_cnpj[:3] or comp_cnpj[-2:] == nf_cnpj[-2:]):
            score += 18; motivos.append("CNPJ parcial/mascarado compatível")

    # Valor é o principal quando comprovante está com CNPJ mascarado
    if nf.get("valor") is not None and comp.get("valor") is not None:
        diff = abs(float(nf["valor"]) - float(comp["valor"]))
        if diff <= 0.01:
            score += 40; motivos.append("Valor igual")
        elif diff <= 1:
            score += 30; motivos.append("Valor próximo")
        elif diff <= 10:
            score += 15; motivos.append("Valor com pequena diferença")

    nome_score = fuzz.token_set_ratio(str(nf.get("fornecedor") or "").upper(), str(comp.get("beneficiario") or "").upper())
    if nome_score >= 90:
        score += 25; motivos.append("Nome muito parecido")
    elif nome_score >= 75:
        score += 18; motivos.append("Nome parecido")
    elif nome_score >= 58:
        score += 10; motivos.append("Nome parcialmente parecido")

    # Busca tokens relevantes do fornecedor no texto inteiro do comprovante
    forn_tokens = [t for t in re.findall(r"[A-Z0-9]{4,}", str(nf.get("fornecedor") or "").upper()) if t not in {"LTDA", "SERVICOS", "ADMINISTRATIVOS", "ME", "EPP"}]
    texto_comp = str(comp.get("texto") or "").upper()
    hits = sum(1 for t in forn_tokens if t in texto_comp)
    if hits >= 2:
        score += 8; motivos.append("Tokens do fornecedor no comprovante")

    return min(score, 100), "; ".join(motivos), nome_score

def criar_ranking(nfs, comps):
    linhas = []
    for i, nf in enumerate(nfs):
        for j, comp in enumerate(comps):
            sc, motivos, nome_score = score_match(nf, comp)
            linhas.append({"nf_idx": i, "comp_idx": j, "score": sc, "motivos": motivos, "nome_score": nome_score})
    return pd.DataFrame(linhas).sort_values(["nf_idx", "score"], ascending=[True, False])

def conciliar_auto(nfs, comps, limite):
    ranking = criar_ranking(nfs, comps)
    usados = set(); saida = []
    for i, nf in enumerate(nfs):
        cand = ranking[(ranking.nf_idx == i) & (~ranking.comp_idx.isin(usados))].head(1)
        if cand.empty:
            saida.append({"status":"NF sem comprovante", "score":0, "motivos":"", "nf":nf, "comprovante":None, "comp_idx":None})
            continue
        row = cand.iloc[0]
        comp = comps[int(row.comp_idx)]
        status = "Conciliado" if row.score >= limite else ("Conferir manualmente" if row.score >= 45 else "NF sem comprovante")
        if status == "Conciliado": usados.add(int(row.comp_idx))
        saida.append({"status":status, "score":int(row.score), "motivos":row.motivos, "nf":nf, "comprovante":comp, "comp_idx":int(row.comp_idx)})
    return saida, ranking, usados

# -------------------- geração arquivos --------------------
def juntar_pdfs(nf_arq, comp_arq, pasta_saida, nf):
    pasta_saida.mkdir(parents=True, exist_ok=True)
    nome = f"NF_{safe_name(nf.get('numero_nf'))}_{safe_name(nf.get('fornecedor'))}.pdf"
    out = pasta_saida / nome
    writer = PdfWriter()
    for arq in [nf_arq, comp_arq]:
        reader = PdfReader(str(arq))
        for page in reader.pages: writer.add_page(page)
    with open(out, "wb") as f: writer.write(f)
    return str(out)

def zipar_pasta(pasta, destino):
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for f in pasta.rglob("*"):
            if f.is_file(): z.write(f, f.relative_to(pasta))
    return destino

# -------------------- UI --------------------
st.title("Conciliador de NF x Comprovante")
st.caption("V1.2 - associação melhorada + pasta de saída + escolha manual")

with st.expander("Configurações de saída", expanded=True):
    pasta_saida_nome = st.text_input("Nome ou caminho da pasta para salvar os PDFs", value="saida_conciliacao")
    st.info("No Streamlit Cloud, a pasta é criada dentro do ambiente do app. Para salvar no seu computador, baixe o ZIP ao final.")

col1, col2, col3 = st.columns([1,1,0.7])
with col1:
    nfs_up = st.file_uploader("PDFs das NFs", type=["pdf"], accept_multiple_files=True)
with col2:
    comps_up = st.file_uploader("PDF/lote de comprovantes", type=["pdf"], accept_multiple_files=True)
with col3:
    limite = st.slider("Score automático", 0, 100, 70)

processar = st.button("Processar", type="primary")

if processar:
    if not nfs_up or not comps_up:
        st.error("Envie as NFs e o lote de comprovantes.")
        st.stop()

    pasta_saida = Path(pasta_saida_nome)
    if not pasta_saida.is_absolute(): pasta_saida = BASE_DIR / pasta_saida_nome
    pastas = {
        "base": pasta_saida,
        "conciliados": pasta_saida / "conciliados",
        "manual": pasta_saida / "conferir_manual",
        "sem_nf": pasta_saida / "comprovantes_sem_nf",
        "rel": pasta_saida / "relatorios",
    }
    for p in pastas.values(): p.mkdir(parents=True, exist_ok=True)

    nf_paths = salvar_uploads(nfs_up, DIR_TEMP / "nfs")
    comp_paths = salvar_uploads(comps_up, DIR_TEMP / "comprovantes")

    with st.spinner("Lendo NFs..."):
        nfs = [extrair_nf(p) for p in nf_paths]
    with st.spinner("Lendo e separando comprovantes..."):
        comps = []
        for p in comp_paths: comps.extend(extrair_comprovantes(p, pastas["sem_nf"]))
    with st.spinner("Associando..."):
        resultado, ranking, usados_auto = conciliar_auto(nfs, comps, limite)

    # Permite seleção manual para itens não conciliados
    st.session_state["resultado"] = resultado
    st.session_state["nfs"] = nfs
    st.session_state["comps"] = comps
    st.session_state["ranking"] = ranking
    st.session_state["pasta_saida"] = str(pasta_saida)

if "resultado" in st.session_state:
    nfs = st.session_state["nfs"]; comps = st.session_state["comps"]
    resultado = st.session_state["resultado"]; ranking = st.session_state["ranking"]
    pasta_saida = Path(st.session_state["pasta_saida"])

    st.subheader("Resumo")
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("NFs lidas", len(nfs)); c2.metric("Comprovantes lidos", len(comps))
    c3.metric("Conciliados automáticos", sum(1 for r in resultado if r["status"] == "Conciliado"))
    c4.metric("Para conferir", sum(1 for r in resultado if r["status"] != "Conciliado"))

    st.subheader("Conferência e escolha manual")
    selecoes = {}
    usados_escolhidos = set(r["comp_idx"] for r in resultado if r["status"] == "Conciliado" and r["comp_idx"] is not None)

    for idx, r in enumerate(resultado):
        nf = r["nf"]
        with st.expander(f"NF {nf.get('numero_nf') or idx+1} - {nf.get('fornecedor')} - {moeda(nf.get('valor'))} | {r['status']} | Score {r['score']}", expanded=(r["status"] != "Conciliado")):
            st.write(f"**CNPJ NF:** {nf.get('cnpj')} | **Valor:** {moeda(nf.get('valor'))}")
            top = ranking[ranking.nf_idx == idx].head(8)
            opcoes = ["Manter automático / sem alteração", "Sem comprovante"]
            mapa = {}
            for _, row in top.iterrows():
                comp = comps[int(row.comp_idx)]
                label = f"Score {int(row.score)} | pág {comp['pagina']} | {comp.get('beneficiario')} | {moeda(comp.get('valor'))} | {comp.get('data_pagamento')} | {row.motivos}"
                opcoes.append(label); mapa[label] = int(row.comp_idx)
            escolha = st.selectbox("Escolher comprovante", opcoes, key=f"sel_{idx}")
            if escolha in mapa: selecoes[idx] = mapa[escolha]
            elif escolha == "Sem comprovante": selecoes[idx] = None

    if st.button("Gerar PDFs finais e relatório", type="primary"):
        conciliados_dir = pasta_saida / "conciliados"
        rel_dir = pasta_saida / "relatorios"
        conciliados_dir.mkdir(parents=True, exist_ok=True); rel_dir.mkdir(parents=True, exist_ok=True)
        registros = []; usados = set()

        for idx, r in enumerate(resultado):
            nf = r["nf"]
            comp_idx = selecoes.get(idx, r.get("comp_idx") if r["status"] == "Conciliado" else None)
            comp = comps[comp_idx] if comp_idx is not None else None
            pdf_final = ""; status = "NF sem comprovante"; score = r.get("score", 0)
            if comp:
                pdf_final = juntar_pdfs(nf["arquivo"], comp["arquivo_pagina"], conciliados_dir, nf)
                status = "Conciliado manual" if idx in selecoes else "Conciliado"
                usados.add(comp_idx)
            registros.append({
                "status": status, "score": score, "motivos": r.get("motivos", ""),
                "nf_numero": nf.get("numero_nf", ""), "nf_fornecedor": nf.get("fornecedor", ""), "nf_cnpj": nf.get("cnpj", ""), "nf_valor": nf.get("valor", ""), "arquivo_nf": nf.get("arquivo", ""),
                "comprovante_beneficiario": comp.get("beneficiario", "") if comp else "", "comprovante_cnpj": comp.get("cnpj", "") if comp else "", "comprovante_valor": comp.get("valor", "") if comp else "", "data_pagamento": comp.get("data_pagamento", "") if comp else "", "arquivo_comprovante": comp.get("arquivo_pagina", "") if comp else "",
                "pdf_final": pdf_final
            })
        for j, comp in enumerate(comps):
            if j not in usados:
                registros.append({"status":"Comprovante sem NF", "score":"", "motivos":"", "nf_numero":"", "nf_fornecedor":"", "nf_cnpj":"", "nf_valor":"", "arquivo_nf":"", "comprovante_beneficiario":comp.get("beneficiario", ""), "comprovante_cnpj":comp.get("cnpj", ""), "comprovante_valor":comp.get("valor", ""), "data_pagamento":comp.get("data_pagamento", ""), "arquivo_comprovante":comp.get("arquivo_pagina", ""), "pdf_final":""})

        df = pd.DataFrame(registros)
        rel = rel_dir / f"relatorio_conciliacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(rel, index=False)
        zip_path = pasta_saida / f"resultado_conciliacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        zipar_pasta(pasta_saida, zip_path)

        st.success(f"Arquivos gerados em: {pasta_saida}")
        st.dataframe(df, use_container_width=True)
        with open(rel, "rb") as f:
            st.download_button("Baixar relatório Excel", f, file_name=rel.name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with open(zip_path, "rb") as f:
            st.download_button("Baixar todos os PDFs e relatório em ZIP", f, file_name=zip_path.name, mime="application/zip")
