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
BASE_DIR=Path(__file__).parent
DIR_NFS=BASE_DIR/'entrada'/'nfs'; DIR_COMP=BASE_DIR/'entrada'/'comprovantes'; DIR_TMP=BASE_DIR/'temporario'; DIR_REL=BASE_DIR/'relatorios'; DIR_SAIDA=BASE_DIR/'saida'/'conciliados'
for p in [DIR_NFS,DIR_COMP,DIR_TMP,DIR_REL,DIR_SAIDA]: p.mkdir(parents=True, exist_ok=True)
STOPWORDS={"LTDA","EIRELI","ME","EPP","SA","S/A","SERVICOS","SERVIÇOS","SERVICO","SERVIÇO","ADMINISTRATIVOS","ADMINISTRATIVO","COMERCIO","COMÉRCIO","INDUSTRIA","INDÚSTRIA","REPRESENTACOES","REPRESENTAÇÕES","PRODUTOS","MEDICOS","MÉDICOS","HOSPITALARES","CONSULTORIA","EMPRESARIAL","DE","DA","DO","DAS","DOS","E","EM","PARA","COM"}

def limpar_texto(t): return re.sub(r"\s+"," ",t or "").strip()
def remover_acentos(t):
    t=unicodedata.normalize('NFKD', str(t or '')); return ''.join(c for c in t if not unicodedata.combining(c))
def normalizar_nome(t):
    t=remover_acentos(str(t or '')).upper(); t=re.sub(r'[^A-Z0-9 ]',' ',t)
    return ' '.join(p for p in t.split() if p not in STOPWORDS and len(p)>1)
def tokens_relevantes(t): return {x for x in normalizar_nome(t).split() if len(x)>=4 and not x.isdigit() and x not in STOPWORDS}
def somente_numeros(t): return re.sub(r'\D','',str(t or ''))
def normalizar_valor(t):
    if t is None: return None
    t=str(t).replace('R$','').strip().replace('.','').replace(',','.')
    try: return round(float(t),2)
    except Exception: return None
def formatar_valor(v):
    if v is None or v=='': return ''
    try: return f"R$ {float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X','.')
    except Exception: return str(v)
def limpar_nome_arquivo(t):
    t=normalizar_nome(t) or 'SEM_NOME'; t=re.sub(r'[^A-Z0-9_ -]','',t); return re.sub(r'\s+','_',t).strip('_')[:90]

def ler_pdf_texto(caminho):
    texto=''
    try:
        with pdfplumber.open(caminho) as pdf:
            for pag in pdf.pages: texto+='\n'+(pag.extract_text() or '')
    except Exception: return ''
    return limpar_texto(texto)
def extrair_cnpjs(t): return re.findall(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', t or '')
def extrair_cpfs(t): return re.findall(r'\d{3}\.\d{3}\.\d{3}-\d{2}', t or '')
def extrair_datas(t): return re.findall(r'\d{2}/\d{2}/\d{4}', t or '')
def extrair_numero_nf(t):
    for p in [r'N[uú]mero da Nota\s*(\d+)', r'RPS\s*N[ºo°\. ]*\s*(\d+)', r'NFS-e\s*(\d{4,})', r'N[ºo°\. ]+\s*(\d{4,})']:
        m=re.search(p,t,re.I)
        if m: return m.group(1).lstrip('0') or m.group(1)
    return ''
def extrair_fornecedor_nf(t):
    ms=re.findall(r'Nome/Raz[aã]o Social:\s*(.*?)\s*Endere[cç]o:', t, re.I)
    if ms: return limpar_texto(ms[0])
    c=extrair_cnpjs(t)
    if c:
        trecho=t[t.find(c[0]):t.find(c[0])+350]
        m=re.search(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\s+[\d\.\-]*\s*(.*?)\s+(?:AV|RUA|AL|TRAV|ESTR|ROD|PCA|PRAÇA)\s', trecho, re.I)
        if m: return limpar_texto(m.group(1))
    return ''
def extrair_valor_bruto_nf(t):
    for p in [r'VALOR TOTAL DO SERVI[ÇC]O\s*=\s*R\$\s*([\d\.\,]+)', r'Valor Total da Nota\s*R?\$?\s*([\d\.\,]+)', r'Valor Total.*?R\$\s*([\d\.\,]+)', r'TOTAL\s*R\$\s*([\d\.\,]+)']:
        m=re.search(p,t,re.I)
        if m: return normalizar_valor(m.group(1))
    return None
def extrair_valor_campo(t, nomes):
    for nome in nomes:
        for p in [rf'{nome}[^0-9]{{0,25}}R?\$?\s*([\d\.\,]+)', rf'{nome}\s*\(R\$\)\s*([\d\.\,]+)']:
            m=re.search(p,t,re.I)
            if m:
                v=normalizar_valor(m.group(1))
                if v is not None: return v
    return 0.0
def extrair_retencoes_nf(t):
    campos={'inss':['INSS'],'irrf':['IRRF','IR'],'csll':['CSLL'],'cofins':['COFINS'],'pis':['PIS/PASEP','PIS'],'iss':['ISS','Valor do ISS']}
    ret={k:extrair_valor_campo(t,v) for k,v in campos.items()}; ret['total_retencoes']=round(sum(ret.values()),2); return ret
def ler_nf(caminho):
    texto=ler_pdf_texto(caminho); cnpjs=extrair_cnpjs(texto); forn=extrair_fornecedor_nf(texto); bruto=extrair_valor_bruto_nf(texto); ret=extrair_retencoes_nf(texto)
    liq=round(max(bruto-ret.get('total_retencoes',0),0),2) if bruto is not None else None; datas=extrair_datas(texto)
    return {'arquivo':str(caminho),'texto':texto,'numero_nf':extrair_numero_nf(texto),'fornecedor':forn,'fornecedor_norm':normalizar_nome(forn),'cnpj':cnpjs[0] if cnpjs else '', 'valor_bruto':bruto,'valor_liquido_estimado':liq,'retencoes':ret,'data_emissao_ou_primeira_data':datas[0] if datas else '', 'tokens_nome':tokens_relevantes(forn),'tokens_texto':tokens_relevantes(texto)}

def salvar_pagina_pdf(pdf_origem,i,pasta):
    pasta.mkdir(parents=True, exist_ok=True); reader=PdfReader(str(pdf_origem)); writer=PdfWriter(); writer.add_page(reader.pages[i]); saida=pasta/f'{Path(pdf_origem).stem}_pagina_{i+1:03d}.pdf'
    with open(saida,'wb') as f: writer.write(f)
    return str(saida)
def extrair_valor_comp(t):
    for p in [r'VALOR COBRADO\s*([\d\.\,]+)', r'VALOR DO DOCUMENTO\s*([\d\.\,]+)', r'VALOR:\s*R?\$?\s*([\d\.\,]+)', r'VALOR\s*([\d\.\,]+)']:
        m=re.search(p,t,re.I)
        if m: return normalizar_valor(m.group(1))
    return None
def extrair_beneficiario(t):
    for p in [r'PAGO PARA:\s*(.*?)\s*(?:CNPJ|CPF|CHAVE PIX|INSTITUICAO|INSTITUIÇÃO)', r'BENEFICIARIO:\s*(.*?)\s*(?:CPF/CNPJ|CNPJ|NOME FANTASIA|BANCO|BENEFICIARIO FINAL)', r'BENEFICIÁRIO:\s*(.*?)\s*(?:CPF/CNPJ|CNPJ|NOME FANTASIA|BANCO|BENEFICIÁRIO FINAL)']:
        m=re.search(p,t,re.I)
        if m: return limpar_texto(m.group(1))
    return ''
def extrair_doc_comp(t):
    docs=extrair_cnpjs(t)+extrair_cpfs(t); docs=[d for d in docs if somente_numeros(d) not in {'02629588000172','002629588000172'}]
    if docs: return docs[0]
    m=re.search(r'\d{3}\.\*{3}\.\*{3}/\*{4}-\d{2}', t or '')
    return m.group(0) if m else ''
def extrair_data_comp(t):
    for p in [r'DATA DO PAGAMENTO\s*(\d{2}/\d{2}/\d{4})', r'DATA DA TRANSFERENCIA:\s*(\d{2}/\d{2}/\d{4})', r'DATA:\s*(\d{2}/\d{2}/\d{4})']:
        m=re.search(p,t,re.I)
        if m: return m.group(1)
    d=extrair_datas(t); return d[0] if d else ''
def extrair_documento_banco(t):
    m=re.search(r'NR\.?\s*DOCUMENTO[: ]+\s*([\d\.]+)',t,re.I) or re.search(r'DOCUMENTO:\s*([\d\.]+)',t,re.I)
    return m.group(1) if m else ''
def ler_comprovantes(caminho):
    comps=[]; pasta=DIR_TMP/'paginas_comprovantes'
    with pdfplumber.open(caminho) as pdf:
        for i,pag in enumerate(pdf.pages):
            texto=limpar_texto(pag.extract_text() or ''); arq=salvar_pagina_pdf(caminho,i,pasta); benef=extrair_beneficiario(texto)
            comps.append({'arquivo_lote':str(caminho),'arquivo_pagina':arq,'pagina':i+1,'texto':texto,'beneficiario':benef,'beneficiario_norm':normalizar_nome(benef),'cnpj_cpf':extrair_doc_comp(texto),'valor':extrair_valor_comp(texto),'data_pagamento':extrair_data_comp(texto),'documento_banco':extrair_documento_banco(texto),'tokens_nome':tokens_relevantes(benef),'tokens_texto':tokens_relevantes(texto)})
    return comps

def valor_score(nf,comp):
    pago=comp.get('valor')
    if pago is None: return 0,'Valor do comprovante não lido'
    melhor=(0,'')
    for tipo,val in [('bruto',nf.get('valor_bruto')),('liquido_estimado',nf.get('valor_liquido_estimado'))]:
        if val is None: continue
        diff=abs(float(val)-float(pago))
        if diff<=0.01: return 45,f'Valor pago igual ao valor {tipo} da NF'
        if diff<=1: melhor=max(melhor,(38,f'Valor próximo do valor {tipo}, diferença até R$ 1'), key=lambda x:x[0])
        elif val and diff/float(val)<=0.02: melhor=max(melhor,(25,f'Valor próximo do valor {tipo}, diferença até 2%'), key=lambda x:x[0])
        elif val and diff/float(val)<=0.05: melhor=max(melhor,(12,f'Valor com diferença até 5% do valor {tipo}'), key=lambda x:x[0])
    return melhor
def doc_score(nf,comp):
    nfdoc=somente_numeros(nf.get('cnpj')); raw=comp.get('cnpj_cpf',''); cd=somente_numeros(raw)
    if not nfdoc or not raw: return 0,'CNPJ ausente'
    if '*' in str(raw):
        ini=cd[:3]; fim=cd[-2:]
        if nfdoc.startswith(ini[-2:]) or nfdoc.startswith(ini) or nfdoc.endswith(fim): return 16,'CNPJ mascarado parcialmente compatível'
        return 0,'CNPJ mascarado sem compatibilidade'
    if nfdoc==cd: return 25,'CNPJ/CPF igual'
    if len(cd)>=6 and (nfdoc.startswith(cd[:3]) or nfdoc.endswith(cd[-2:])): return 10,'CNPJ/CPF parcialmente compatível'
    return 0,'CNPJ diferente'
def nome_score(nf,comp):
    sim=max(fuzz.token_set_ratio(nf.get('fornecedor_norm',''),comp.get('beneficiario_norm','')), fuzz.partial_ratio(nf.get('fornecedor_norm',''),comp.get('beneficiario_norm','')))
    pontos=0; motivo=''
    if sim>=92: pontos=25; motivo=f'Nome muito parecido ({sim:.0f}%)'
    elif sim>=82: pontos=20; motivo=f'Nome parecido ({sim:.0f}%)'
    elif sim>=68: pontos=12; motivo=f'Nome parcialmente parecido ({sim:.0f}%)'
    elif sim>=55: pontos=6; motivo=f'Nome com baixa semelhança ({sim:.0f}%)'
    fortes=[t for t in (nf.get('tokens_nome',set()) & comp.get('tokens_nome',set())) if len(t)>=5]
    if len(fortes)>=2: pontos+=10; motivo+=' | 2+ palavras do nome em comum'
    elif len(fortes)==1: pontos+=5; motivo+=' | 1 palavra forte do nome em comum'
    return min(pontos,30), motivo.strip(' |'), sim
def texto_score(nf,comp):
    sim=fuzz.token_set_ratio(normalizar_nome(nf.get('texto',''))[:4000], normalizar_nome(comp.get('texto',''))[:4000]); fortes=[t for t in (nf.get('tokens_texto',set()) & comp.get('tokens_texto',set())) if len(t)>=5]
    pontos=0; motivos=[]
    if sim>=70: pontos+=10; motivos.append(f'Texto completo parecido ({sim:.0f}%)')
    elif sim>=55: pontos+=5; motivos.append(f'Texto completo parcialmente parecido ({sim:.0f}%)')
    if len(fortes)>=5: pontos+=12; motivos.append('5+ palavras fortes em comum no texto')
    elif len(fortes)>=3: pontos+=8; motivos.append('3+ palavras fortes em comum no texto')
    elif len(fortes)>=1: pontos+=3; motivos.append('palavras fortes em comum no texto')
    return min(pontos,20), ' | '.join(motivos), sim
def nf_numero_score(nf,comp):
    num=somente_numeros(nf.get('numero_nf')); txt=somente_numeros(comp.get('texto'))
    return (8,'Número NF/RPS localizado no comprovante') if num and len(num)>=2 and num in txt else (0,'')
def score_match(nf,comp):
    pts=0; motivos=[]
    for func in [valor_score, doc_score]:
        p,m=func(nf,comp); pts+=p
        if m and p>0: motivos.append(m)
    p,m,simn=nome_score(nf,comp); pts+=p
    if m: motivos.append(m)
    p,m,simt=texto_score(nf,comp); pts+=p
    if m: motivos.append(m)
    p,m=nf_numero_score(nf,comp); pts+=p
    if m: motivos.append(m)
    if comp.get('valor') is None: pts=min(pts,45); motivos.append('Trava: comprovante sem valor lido')
    return min(pts,100),' | '.join(motivos),simn,simt
def conciliar(nfs,comps,lim_auto,lim_manual):
    res=[]; usados=set()
    for nf in nfs:
        cands=[]
        for comp in comps:
            if comp['arquivo_pagina'] in usados: continue
            cands.append((*score_match(nf,comp), comp))
        cands.sort(key=lambda x:x[0], reverse=True); sc,mot,simn,simt,comp=(cands[0] if cands else (0,'',0,0,None))
        empate=len(cands)>1 and cands[1][0]>=sc-5 and sc>=lim_manual
        if comp and sc>=lim_auto and not empate: status='Conciliado'; usados.add(comp['arquivo_pagina'])
        elif comp and sc>=lim_manual: status='Conferir manualmente'
        else: status='NF sem comprovante'
        res.append({'status':status,'score':sc,'motivos':mot,'similaridade_nome':round(simn,2),'similaridade_texto':round(simt,2),'nf':nf,'comprovante':comp,'top_candidatos':cands[:7],'empate_proximo':empate})
    return res,usados

def unir_pdfs(nf,comp,pasta):
    pasta=Path(pasta); pasta.mkdir(parents=True, exist_ok=True); caminho=pasta/f"NF_{limpar_nome_arquivo(nf.get('numero_nf'))}_{limpar_nome_arquivo(nf.get('fornecedor'))}.pdf"; writer=PdfWriter()
    for arq in [nf['arquivo'], comp['arquivo_pagina']]:
        reader=PdfReader(str(arq))
        for page in reader.pages: writer.add_page(page)
    with open(caminho,'wb') as f: writer.write(f)
    return str(caminho)
def zipar_pasta(pasta):
    mem=BytesIO(); pasta=Path(pasta)
    with zipfile.ZipFile(mem,'w',zipfile.ZIP_DEFLATED) as z:
        for arq in pasta.rglob('*'):
            if arq.is_file(): z.write(arq, arq.relative_to(pasta))
    mem.seek(0); return mem
def linha(status,score,mot,simn,simt,nf,comp,pdf='',tipo='Resultado',empate=False):
    return {'tipo_linha':tipo,'status':status,'score':score,'empate_proximo':'Sim' if empate else 'Não','motivos_match':mot,'similaridade_nome':simn,'similaridade_texto':simt,'nf_numero':nf.get('numero_nf','') if nf else '','nf_fornecedor':nf.get('fornecedor','') if nf else '','nf_cnpj':nf.get('cnpj','') if nf else '','nf_valor_bruto':nf.get('valor_bruto','') if nf else '','nf_valor_bruto_formatado':formatar_valor(nf.get('valor_bruto')) if nf else '','nf_valor_liquido_estimado':nf.get('valor_liquido_estimado','') if nf else '','nf_valor_liquido_formatado':formatar_valor(nf.get('valor_liquido_estimado')) if nf else '','nf_retencoes_estimadas':nf.get('retencoes',{}).get('total_retencoes','') if nf else '','nf_data':nf.get('data_emissao_ou_primeira_data','') if nf else '','comprovante_beneficiario':comp.get('beneficiario','') if comp else '','comprovante_doc':comp.get('cnpj_cpf','') if comp else '','comprovante_valor':comp.get('valor','') if comp else '','comprovante_valor_formatado':formatar_valor(comp.get('valor')) if comp else '','data_pagamento':comp.get('data_pagamento','') if comp else '','documento_banco':comp.get('documento_banco','') if comp else '','pagina_comprovante':comp.get('pagina','') if comp else '','arquivo_nf':nf.get('arquivo','') if nf else '','arquivo_comprovante':comp.get('arquivo_pagina','') if comp else '','pdf_final':pdf,'texto_nf_resumo':(nf.get('texto','')[:700] if nf and nf.get('texto') else ''),'texto_comprovante_resumo':(comp.get('texto','')[:700] if comp and comp.get('texto') else '')}

st.title('Conciliador NF x Comprovante')
st.caption('V1.5 - match financeiro: CNPJ opcional, valor bruto/líquido estimado, texto completo e ranking.')
col1,col2=st.columns(2)
with col1: arquivos_nf=st.file_uploader('Enviar PDFs das NFs', type=['pdf'], accept_multiple_files=True)
with col2: arquivos_comp=st.file_uploader('Enviar lote(s) de comprovantes', type=['pdf'], accept_multiple_files=True)
with st.expander('Configurações de conciliação', expanded=True):
    c1,c2,c3=st.columns(3); lim_auto=c1.slider('Score para conciliar automático',0,100,66); lim_manual=c2.slider('Score para enviar à conferência',0,100,35); pasta_saida_txt=c3.text_input('Pasta de saída dos PDFs unidos', value=str(DIR_SAIDA))
    st.info('O CNPJ não é obrigatório. O valor tem peso maior e o sistema compara bruto, líquido estimado, nome e texto completo da NF.')
if st.button('Processar conciliação', type='primary'):
    for pasta in [DIR_NFS,DIR_COMP,DIR_TMP]:
        if pasta.exists():
            for arq in pasta.rglob('*'):
                if arq.is_file(): arq.unlink()
        pasta.mkdir(parents=True, exist_ok=True)
    if not arquivos_nf or not arquivos_comp: st.error('Envie pelo menos uma NF e um lote de comprovantes.'); st.stop()
    for arq in arquivos_nf: (DIR_NFS/arq.name).write_bytes(arq.read())
    for arq in arquivos_comp: (DIR_COMP/arq.name).write_bytes(arq.read())
    with st.spinner('Lendo NFs...'): nfs=[ler_nf(p) for p in DIR_NFS.glob('*.pdf')]
    with st.spinner('Lendo e separando comprovantes...'):
        comps=[]
        for p in DIR_COMP.glob('*.pdf'): comps.extend(ler_comprovantes(p))
    with st.spinner('Calculando associação financeira...'): resultados,usados=conciliar(nfs,comps,lim_auto,lim_manual)
    registros=[]; pasta_saida=Path(pasta_saida_txt); pasta_saida.mkdir(parents=True, exist_ok=True)
    for r in resultados:
        nf,comp=r['nf'],r['comprovante']; pdf=''
        if r['status']=='Conciliado' and comp: pdf=unir_pdfs(nf,comp,pasta_saida)
        registros.append(linha(r['status'],r['score'],r['motivos'],r['similaridade_nome'],r['similaridade_texto'],nf,comp,pdf,'Resultado',r['empate_proximo']))
        for pos,cand in enumerate(r['top_candidatos'],1):
            sc,mot,simn,simt,c=cand; registros.append(linha(f'Candidato {pos}',sc,mot,round(simn,2),round(simt,2),nf,c,'',f'Candidato {pos}',False))
    for c in comps:
        if c['arquivo_pagina'] not in usados: registros.append(linha('Comprovante sem NF automática','','','','',None,c,'','Sobra comprovante',False))
    df=pd.DataFrame(registros); rel=DIR_REL/f"relatorio_conciliacao_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    with pd.ExcelWriter(rel, engine='openpyxl') as writer:
        df.to_excel(writer,index=False,sheet_name='Resultado')
        pd.DataFrame(nfs).drop(columns=['texto','tokens_nome','tokens_texto'], errors='ignore').to_excel(writer,index=False,sheet_name='NFs_lidas')
        pd.DataFrame(comps).drop(columns=['texto','tokens_nome','tokens_texto'], errors='ignore').to_excel(writer,index=False,sheet_name='Comprovantes_lidos')
    st.success('Processamento concluído.')
    m1,m2,m3,m4=st.columns(4); m1.metric('NFs lidas',len(nfs)); m2.metric('Comprovantes lidos',len(comps)); m3.metric('Conciliados',len([r for r in resultados if r['status']=='Conciliado'])); m4.metric('Conferir manualmente',len([r for r in resultados if r['status']=='Conferir manualmente']))
    st.dataframe(df,use_container_width=True)
    with open(rel,'rb') as f: st.download_button('Baixar relatório Excel', f, file_name=rel.name)
    st.download_button('Baixar PDFs conciliados em ZIP', data=zipar_pasta(pasta_saida), file_name='pdfs_conciliados.zip', mime='application/zip')
