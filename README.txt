V1.5.5 - Correção compatibilidade pandas

Correção:
- Troca df.applymap por df.map quando disponível.
- Mantém fallback para versões antigas do pandas.
- Mantém limpeza de caracteres ilegais no Excel.

Atualização:
1. Substitua app.py
2. Substitua requirements.txt
3. Faça Reboot app no Streamlit Cloud
