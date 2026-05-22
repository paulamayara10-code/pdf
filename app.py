# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

from utils.leitor_nf import ler_nf_pdf
from utils.leitor_comprovante import separar_e_ler_comprovantes
from utils.conciliador import conciliar_nfs_com_comprovantes
from utils.pdf_manager import juntar_nf_com_comprovante


st.set_page_config(page_title="Conciliador NF x Comprovante", layout="wide")

BASE_DIR = Path(__file__).parent
DIR_NFS = BASE_DIR / "entrada" / "nfs"
DIR_COMP = BASE_DIR / "entrada" / "comprovantes"
DIR_SAIDA = BASE_DIR / "saida"
DIR_REL = BASE_DIR / "relatorios"

for pasta in [
    DIR_NFS,
    DIR_COMP,
    DIR_SAIDA / "conciliados",
    DIR_SAIDA / "conferir_manual",
    DIR_SAIDA / "sem_comprovante",
    DIR_SAIDA / "comprovantes_sem_nf",
    DIR_REL
]:
    pasta.mkdir(parents=True, exist_ok=True)


st.title("Conciliador de NF x Comprovante")
st.caption("Versão inicial: lê NFs, separa comprovantes bancários, cruza por CNPJ/nome/valor e gera relatório.")

col1, col2 = st.columns(2)

with col1:
    arquivos_nf = st.file_uploader(
        "Enviar PDFs das Notas Fiscais",
        type=["pdf"],
        accept_multiple_files=True
    )

with col2:
    arquivos_comp = st.file_uploader(
        "Enviar lote de comprovantes do banco",
        type=["pdf"],
        accept_multiple_files=True
    )

limite_score = st.slider("Score mínimo para conciliar automaticamente", 0, 100, 75)

if st.button("Processar conciliação", type="primary"):
    for pasta in [DIR_NFS, DIR_COMP]:
        for arq in pasta.glob("*"):
            if arq.is_file():
                arq.unlink()

    if not arquivos_nf:
        st.error("Envie pelo menos uma NF em PDF.")
        st.stop()

    if not arquivos_comp:
        st.error("Envie pelo menos um arquivo de comprovantes.")
        st.stop()

    with st.spinner("Salvando arquivos..."):
        for arq in arquivos_nf:
            (DIR_NFS / arq.name).write_bytes(arq.read())
        for arq in arquivos_comp:
            (DIR_COMP / arq.name).write_bytes(arq.read())

    with st.spinner("Lendo NFs..."):
        nfs = [ler_nf_pdf(pdf) for pdf in DIR_NFS.glob("*.pdf")]

    with st.spinner("Separando e lendo comprovantes..."):
        comprovantes = []
        for pdf in DIR_COMP.glob("*.pdf"):
            comprovantes.extend(separar_e_ler_comprovantes(pdf, DIR_SAIDA / "comprovantes_sem_nf"))

    with st.spinner("Conciliando..."):
        resultado = conciliar_nfs_com_comprovantes(nfs, comprovantes, limite_score=limite_score)

    registros = []
    usados_comprovantes = set()

    with st.spinner("Gerando PDFs unidos e relatório..."):
        for item in resultado:
            nf = item["nf"]
            comp = item.get("comprovante")
            status = item["status"]
            caminho_pdf_final = ""

            if status == "Conciliado":
                usados_comprovantes.add(comp["arquivo_pagina"])
                caminho_pdf_final = juntar_nf_com_comprovante(
                    nf["arquivo"],
                    comp["arquivo_pagina"],
                    DIR_SAIDA / "conciliados",
                    nf
                )

            registros.append({
                "status": status,
                "score": item.get("score", 0),
                "nf_numero": nf.get("numero_nf", ""),
                "nf_fornecedor": nf.get("fornecedor", ""),
                "nf_cnpj": nf.get("cnpj", ""),
                "nf_valor": nf.get("valor", ""),
                "arquivo_nf": str(nf.get("arquivo", "")),
                "comprovante_beneficiario": comp.get("beneficiario", "") if comp else "",
                "comprovante_cnpj": comp.get("cnpj", "") if comp else "",
                "comprovante_valor": comp.get("valor", "") if comp else "",
                "data_pagamento": comp.get("data_pagamento", "") if comp else "",
                "arquivo_comprovante": comp.get("arquivo_pagina", "") if comp else "",
                "pdf_final": str(caminho_pdf_final)
            })

        for comp in comprovantes:
            if comp["arquivo_pagina"] not in usados_comprovantes:
                registros.append({
                    "status": "Comprovante sem NF",
                    "score": "",
                    "nf_numero": "",
                    "nf_fornecedor": "",
                    "nf_cnpj": "",
                    "nf_valor": "",
                    "arquivo_nf": "",
                    "comprovante_beneficiario": comp.get("beneficiario", ""),
                    "comprovante_cnpj": comp.get("cnpj", ""),
                    "comprovante_valor": comp.get("valor", ""),
                    "data_pagamento": comp.get("data_pagamento", ""),
                    "arquivo_comprovante": comp.get("arquivo_pagina", ""),
                    "pdf_final": ""
                })

        df = pd.DataFrame(registros)
        nome_relatorio = f"relatorio_conciliacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        caminho_relatorio = DIR_REL / nome_relatorio
        df.to_excel(caminho_relatorio, index=False)

    st.success("Processamento concluído!")

    c1, c2, c3 = st.columns(3)
    c1.metric("NFs lidas", len(nfs))
    c2.metric("Comprovantes lidos", len(comprovantes))
    c3.metric("Conciliados", len(df[df["status"] == "Conciliado"]))

    st.dataframe(df, use_container_width=True)

    with open(caminho_relatorio, "rb") as f:
        st.download_button(
            "Baixar relatório Excel",
            data=f,
            file_name=nome_relatorio,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
