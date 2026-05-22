# Conciliador NF x Comprovante - V1

## O que faz
- Lê PDFs de NF
- Lê lote de comprovantes do banco
- Separa o lote página por página
- Extrai fornecedor, CNPJ, valor e data
- Cruza NF x comprovante
- Gera PDF unido para conciliados
- Gera relatório Excel

## Como rodar

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Regra inicial de conciliação

Score máximo: 100 pontos:
- CNPJ igual: até 50 pontos
- Valor igual: até 35 pontos
- Nome parecido: até 15 pontos

Score recomendado para automático: 75.
