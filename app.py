# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime
import re
import pandas as pd
import streamlit as st
import pdfplumber
from pypdf import PdfReader, PdfWriter
from rapidfuzz import fuzz

st.set_page_config(page_title='Conciliador NF x Comprovante', layout='wide')
BASE_DIR = Path(__file__).parent
DIR_NFS = BASE_DIR / 'entrada' / 'nfs'
DIR_COMP = BASE_DIR / 'entrada' / 'comprovantes'
DIR_SAIDA = BASE_DIR / 'saida'
DIR_REL = BASE_DIR / 'relatorios'
for pasta in [DIR_NFS, DIR_COMP, DIR_SAIDA/'conciliados', DIR_SAIDA/'conferir_manual', DIR_SAIDA/'sem_comprovante', DIR_SAIDA/'comprovantes_sem_nf', DIR_REL]:
    pasta.mkdir(parents=True, exist_ok=True)

def limpar_texto(texto): return re.sub(r'\s+', ' ', texto or '').strip()
def somente_numeros(txt): return re.sub(r'\D', '', str(txt or ''))
def normalizar_valor(valor_txt):
    if not valor_txt: return None
    valor_txt = str(valor_txt).replace('R$', '').strip().replace('.', '').replace(',', '.')
    try: return round(float(valor_txt), 2)
    except Exception: return None

def limpar_nome_arquivo(texto):
    texto = str(texto or 'SEM_NOME').upper()
    texto = re.sub(r'[^A-Z0-9_\- ]', '', texto)
    texto = re.sub(r'\s+', '_', texto).strip('_')
    return texto[:80]

def extrair_numero_nf(texto):
    for p in [r'N[uú]mero da Nota\s*(\d+)', r'Nota\s*(\d{4,})', r'NFS-e\s*(\d{4,})']:
        m = re.search(p, texto, flags=re.I)
        if m: return m.group(1)
    return ''

def extrair_fornecedor_nf(texto):
    m = re.search(r'Nome/Raz[aã]o Social:\s*(.*?)\s*Endere[cç]o:', texto, flags=re.I)
    if m: return limpar_texto(m.group(1))
    m = re.search(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\s+[\d\.\-]+\s+(.*?)\s+AV\s', texto, flags=re.I)
    if m: return limpar_texto(m.group(1))
    return ''

def extrair_valor_nf(texto):
    for p in [r'VALOR TOTAL DO SERVI[ÇC]O\s*=\s*R\$\s*([\d\.\,]+)', r'Valor Total.*?R\$\s*([\d\.\,]+)', r'VALOR.*?R\$\s*([\d\.\,]+)']:
        m = re.search(p, texto, flags=re.I)
        if m: return normalizar_valor(m.group(1))
    return None

def ler_nf_pdf(caminho_pdf):
    caminho_pdf = Path(caminho_pdf); texto = ''
    try:
        with pdfplumber.open(caminho_pdf) as pdf:
            for pagina in pdf.pages: texto += '\n' + (pagina.extract_text() or '')
    except Exception as e:
        return {'arquivo': str(caminho_pdf), 'texto': '', 'numero_nf': '', 'fornecedor': '', 'cnpj': '', 'valor': None, 'erro': str(e)}
    texto = limpar_texto(texto)
    cnpjs = re.findall(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', texto)
    return {'arquivo': str(caminho_pdf), 'texto': texto, 'numero_nf': extrair_numero_nf(texto), 'fornecedor': extrair_fornecedor_nf(texto), 'cnpj': cnpjs[0] if cnpjs else '', 'valor': extrair_valor_nf(texto), 'erro': ''}

def extrair_valor_comprovante(texto):
    for p in [r'VALOR COBRADO\s*([\d\.\,]+)', r'VALOR DO DOCUMENTO\s*([\d\.\,]+)', r'VALOR:\s*R?\$?\s*([\d\.\,]+)', r'VALOR\s*([\d\.\,]+)']:
        m = re.search(p, texto, flags=re.I)
        if m: return normalizar_valor(m.group(1))
    return None

def extrair_cnpj_ou_cpf_comprovante(texto):
    encontrados=[]
    for p in [r'CNPJ:\s*(\d{1,3}\.?\d{3}\.?\d{3}/\d{4}-\d{2})', r'CPF/CNPJ:\s*([\d\.\-/]+)']:
        encontrados.extend(re.findall(p, texto, flags=re.I))
    filtrados=[c for c in encontrados if '02.629.588/0001-72' not in c and '2.629.588/0001-72' not in c and '002.***.***/****-72' not in c]
    return filtrados[0] if filtrados else ''

def extrair_beneficiario_comprovante(texto):
    for p in [r'PAGO PARA:\s*(.*?)\s*(?:CNPJ|CPF|CHAVE PIX|INSTITUICAO)', r'BENEFICIARIO:\s*(.*?)\s*(?:CPF/CNPJ|CNPJ|NOME FANTASIA|BANCO)', r'BENEFICIÁRIO:\s*(.*?)\s*(?:CPF/CNPJ|CNPJ|NOME FANTASIA|BANCO)']:
        m = re.search(p, texto, flags=re.I)
        if m: return limpar_texto(m.group(1))
    return ''

def extrair_data_pagamento(texto):
    for p in [r'DATA DO PAGAMENTO\s*(\d{2}/\d{2}/\d{4})', r'DATA DA TRANSFERENCIA:\s*(\d{2}/\d{2}/\d{4})', r'DATA:\s*(\d{2}/\d{2}/\d{4})']:
        m = re.search(p, texto, flags=re.I)
        if m: return m.group(1)
    return ''

def salvar_pagina_pdf(pdf_origem, numero_pagina, pasta_saida):
    pasta_saida = Path(pasta_saida); pasta_saida.mkdir(parents=True, exist_ok=True)
    reader = PdfReader(str(pdf_origem)); writer = PdfWriter(); writer.add_page(reader.pages[numero_pagina])
    arquivo_saida = pasta_saida / f'{Path(pdf_origem).stem}_pagina_{numero_pagina+1:03d}.pdf'
    with open(arquivo_saida, 'wb') as f: writer.write(f)
    return str(arquivo_saida)

def separar_e_ler_comprovantes(caminho_pdf, pasta_paginas):
    caminho_pdf=Path(caminho_pdf); comprovantes=[]
    with pdfplumber.open(caminho_pdf) as pdf:
        for idx, pagina in enumerate(pdf.pages):
            texto = limpar_texto(pagina.extract_text() or '')
            arquivo_pagina = salvar_pagina_pdf(caminho_pdf, idx, pasta_paginas)
            comprovantes.append({'arquivo_lote': str(caminho_pdf), 'arquivo_pagina': arquivo_pagina, 'pagina': idx+1, 'texto': texto, 'beneficiario': extrair_beneficiario_comprovante(texto), 'cnpj': extrair_cnpj_ou_cpf_comprovante(texto), 'valor': extrair_valor_comprovante(texto), 'data_pagamento': extrair_data_pagamento(texto)})
    return comprovantes

def calcular_score(nf, comp):
    score=0; nf_cnpj=somente_numeros(nf.get('cnpj')); comp_cnpj=somente_numeros(comp.get('cnpj'))
    if nf_cnpj and comp_cnpj:
        if nf_cnpj == comp_cnpj: score += 50
        elif len(comp_cnpj) >= 6 and comp_cnpj[:3] == nf_cnpj[:3] and comp_cnpj[-2:] == nf_cnpj[-2:]: score += 25
    if nf.get('valor') is not None and comp.get('valor') is not None and abs(float(nf.get('valor'))-float(comp.get('valor'))) <= 0.01: score += 35
    sim = fuzz.token_set_ratio(str(nf.get('fornecedor') or '').upper(), str(comp.get('beneficiario') or '').upper())
    if sim >= 85: score += 15
    elif sim >= 70: score += 10
    elif sim >= 55: score += 5
    return min(score, 100)

def conciliar_nfs_com_comprovantes(nfs, comprovantes, limite_score=75):
    resultado=[]; usados=set()
    for nf in nfs:
        melhor=None; melhor_score=-1
        for comp in comprovantes:
            if comp['arquivo_pagina'] in usados: continue
            score=calcular_score(nf, comp)
            if score > melhor_score: melhor_score=score; melhor=comp
        if melhor and melhor_score >= limite_score:
            usados.add(melhor['arquivo_pagina']); status='Conciliado'
        elif melhor and melhor_score >= 50: status='Conferir manualmente'
        else: status='NF sem comprovante'
        resultado.append({'status': status, 'score': melhor_score if melhor else 0, 'nf': nf, 'comprovante': melhor})
    return resultado

def juntar_nf_com_comprovante(arquivo_nf, arquivo_comprovante, pasta_saida, dados_nf):
    pasta_saida=Path(pasta_saida); pasta_saida.mkdir(parents=True, exist_ok=True)
    caminho_saida = pasta_saida / f"NF_{limpar_nome_arquivo(dados_nf.get('numero_nf'))}_{limpar_nome_arquivo(dados_nf.get('fornecedor'))}.pdf"
    writer=PdfWriter()
    for arquivo in [arquivo_nf, arquivo_comprovante]:
        reader=PdfReader(str(arquivo))
        for page in reader.pages: writer.add_page(page)
    with open(caminho_saida, 'wb') as f: writer.write(f)
    return str(caminho_saida)

st.title('Conciliador de NF x Comprovante')
st.caption('Versão 1.1 - arquivo único, sem dependência da pasta utils.')
col1, col2 = st.columns(2)
with col1: arquivos_nf = st.file_uploader('Enviar PDFs das Notas Fiscais', type=['pdf'], accept_multiple_files=True)
with col2: arquivos_comp = st.file_uploader('Enviar lote de comprovantes do banco', type=['pdf'], accept_multiple_files=True)
limite_score = st.slider('Score mínimo para conciliar automaticamente', 0, 100, 75)

if st.button('Processar conciliação', type='primary'):
    for pasta in [DIR_NFS, DIR_COMP]:
        for arq in pasta.glob('*'):
            if arq.is_file(): arq.unlink()
    if not arquivos_nf: st.error('Envie pelo menos uma NF em PDF.'); st.stop()
    if not arquivos_comp: st.error('Envie pelo menos um arquivo de comprovantes.'); st.stop()
    with st.spinner('Salvando arquivos...'):
        for arq in arquivos_nf: (DIR_NFS / arq.name).write_bytes(arq.read())
        for arq in arquivos_comp: (DIR_COMP / arq.name).write_bytes(arq.read())
    with st.spinner('Lendo NFs...'): nfs = [ler_nf_pdf(pdf) for pdf in DIR_NFS.glob('*.pdf')]
    with st.spinner('Separando e lendo comprovantes...'):
        comprovantes=[]
        for pdf in DIR_COMP.glob('*.pdf'): comprovantes.extend(separar_e_ler_comprovantes(pdf, DIR_SAIDA/'comprovantes_sem_nf'))
    with st.spinner('Conciliando...'): resultado = conciliar_nfs_com_comprovantes(nfs, comprovantes, limite_score)
    registros=[]; usados=set()
    with st.spinner('Gerando PDFs unidos e relatório...'):
        for item in resultado:
            nf=item['nf']; comp=item.get('comprovante'); status=item['status']; caminho=''
            if status == 'Conciliado':
                usados.add(comp['arquivo_pagina']); caminho = juntar_nf_com_comprovante(nf['arquivo'], comp['arquivo_pagina'], DIR_SAIDA/'conciliados', nf)
            registros.append({'status': status, 'score': item.get('score',0), 'nf_numero': nf.get('numero_nf',''), 'nf_fornecedor': nf.get('fornecedor',''), 'nf_cnpj': nf.get('cnpj',''), 'nf_valor': nf.get('valor',''), 'arquivo_nf': str(nf.get('arquivo','')), 'comprovante_beneficiario': comp.get('beneficiario','') if comp else '', 'comprovante_cnpj': comp.get('cnpj','') if comp else '', 'comprovante_valor': comp.get('valor','') if comp else '', 'data_pagamento': comp.get('data_pagamento','') if comp else '', 'arquivo_comprovante': comp.get('arquivo_pagina','') if comp else '', 'pdf_final': caminho})
        for comp in comprovantes:
            if comp['arquivo_pagina'] not in usados:
                registros.append({'status': 'Comprovante sem NF', 'score': '', 'nf_numero': '', 'nf_fornecedor': '', 'nf_cnpj': '', 'nf_valor': '', 'arquivo_nf': '', 'comprovante_beneficiario': comp.get('beneficiario',''), 'comprovante_cnpj': comp.get('cnpj',''), 'comprovante_valor': comp.get('valor',''), 'data_pagamento': comp.get('data_pagamento',''), 'arquivo_comprovante': comp.get('arquivo_pagina',''), 'pdf_final': ''})
        df=pd.DataFrame(registros)
        nome=f"relatorio_conciliacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        caminho_rel=DIR_REL/nome; df.to_excel(caminho_rel, index=False)
    st.success('Processamento concluído!')
    c1,c2,c3=st.columns(3); c1.metric('NFs lidas', len(nfs)); c2.metric('Comprovantes lidos', len(comprovantes)); c3.metric('Conciliados', len(df[df['status']=='Conciliado']))
    st.dataframe(df, use_container_width=True)
    with open(caminho_rel, 'rb') as f: st.download_button('Baixar relatório Excel', data=f, file_name=nome, mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
