"""Simulação ponta a ponta sem internet: planilha na memória, Telegram e IA falsos."""
import io
import json
import os
import sys

os.environ.update(TELEGRAM_TOKEN='x', GROQ_KEY='g', APP_SECRET='segredo-teste', PUBLIC_URL='https://ecordena.onrender.com')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ecordena import agenda, app as app_mod, datas, logica, relatorio, store, tg  # noqa: E402

mem = store.MemoriaBackend()
store._backend['b'] = mem
enviados = []
fila_ia = []


def api_falsa(metodo, payload=None, arquivos=None, timeout=30):
    enviados.append((metodo, payload or {}, bool(arquivos)))
    if metodo == 'getMe':
        return {'ok': True, 'result': {'username': 'ecordena_bot'}}
    return {'ok': True, 'result': {}}


def ia_falsa(prompt, como_json=False, audio=None):
    if fila_ia:
        r = fila_ia.pop(0)
        return r
    return 'Seg a sex, 12h às 13h' if not como_json else {'intencao': 'outro', 'resposta': 'ok'}


buf = io.BytesIO()
Image.new('RGB', (1600, 1200), (180, 120, 60)).save(buf, 'JPEG')
tg.api = api_falsa
tg.baixar = lambda fid: buf.getvalue()
logica.ia = ia_falsa
relatorio.ia = ia_falsa

cli = TestClient(app_mod.app)
SEG = app_mod.segredo_telegram()
uid = [0]


def upd(payload):
    uid[0] += 1
    payload['update_id'] = uid[0]
    r = cli.post('/telegram', json=payload, headers={'X-Telegram-Bot-Api-Secret-Token': SEG})
    assert r.status_code == 200, r.text


def msg(chat, texto=None, **extra):
    m = {'chat': {'id': chat, 'type': 'private'}, 'message_id': 1}
    if texto is not None:
        m['text'] = texto
    m.update(extra)
    upd({'message': m})


def bt(chat, data):
    upd({'callback_query': {'id': 'q', 'from': {'id': chat}, 'data': data, 'message': {'chat': {'id': chat}, 'message_id': 5}}})


def mostrar(titulo):
    print(f'\n===== {titulo} =====')
    for metodo, p, arq in enviados:
        txt = (p.get('text') or p.get('caption') or '').replace('\n', ' | ')[:170]
        extra = ' [arquivo]' if arq else ''
        tecl = p.get('reply_markup', {}).get('inline_keyboard')
        if tecl:
            extra += ' [' + ' / '.join(b['text'] for linha in tecl for b in linha)[:110] + ']'
        print(f"  {metodo} {p.get('chat_id', '')}: {txt}{extra}")
    enviados.clear()


# 0) segurança e setup
assert cli.post('/telegram', json={}, headers={'X-Telegram-Bot-Api-Secret-Token': 'errado'}).status_code == 403
assert cli.get('/setup?key=errada').status_code == 403
print(cli.get('/setup?key=segredo-teste').text)
mostrar('setup')

# 1) professor se cadastra e é aprovado na planilha
msg(999, '/start'); bt(999, 'tipo:prof'); msg(999, 'Prof. Gustavo')
s = store.Store()
prof = next(m for m in s.membros() if m['tipo'] == 'prof')
s.set_campo(prof, 'status', 'Aprovado')
print(cli.get('/cron?key=segredo-teste').text)        # primeira foto do painel
mostrar('cadastro do professor')

# 2) aluno se cadastra, prof aprova e escolhe a função
msg(300, '/start'); bt(300, 'tipo:aluno'); msg(300, 'Rodrigo Flávio'); msg(300, 'semana toda 12h-13h')
print(cli.get('/cron?key=segredo-teste').text)        # foto do painel com o Flávio pendente
bt(300, 'ap:300')                                     # aluno tentando se aprovar
bt(999, 'ap:300'); bt(999, 'fn:300:3')
msg(200, '/start'); bt(200, 'tipo:aluno'); msg(200, 'Davi Sales'); msg(200, 'ter e qui à tarde'); bt(999, 'ap:300'); bt(999, 'ap:200')
print('cron depois da aprovação pelo botão (esperado 0 avisos):', cli.get('/cron?key=segredo-teste').text)
mostrar('cadastro dos alunos')

# 3) delegação direta e aberta
H = datas.hoje()
fila_ia.append({'intencao': 'delegar', 'tarefas': [
    {'descricao': 'Fazer a molhagem do lote 5', 'prazo': datas.add_dias(H, 1), 'hora': '14:00', 'responsavel': 'Davi Sales'},
    {'descricao': 'Destorroar o solo', 'prazo': '', 'hora': '', 'responsavel': '', 'candidatos': ['Rodrigo Flávio', 'Davi Sales']}]})
msg(999, 'Davi faz a molhagem amanhã às 14h e preciso que alguém destorroe o solo')
bt(200, 'dl:D001:s')            # pessoa errada
bt(300, 'dl:D001:n')            # Flávio não sabe → vai pro Davi
bt(200, 'dl:D001:s')            # Davi pega
bt(200, 'dl:D001:s')            # clique repetido
mostrar('delegação')

# 4) diário com fotos, /hoje com botões de agenda, /agenda
fila_ia.append({'intencao': 'registrar_atividade', 'atividade': 'Moldei 12 tijolos do lote 6', 'data_atividade': H})
msg(300, 'hoje moldei 12 tijolos do lote 6')
msg(300, photo=[{'file_id': 'peq'}, {'file_id': 'FOTO1'}])
msg(200, photo=[{'file_id': 'FOTO2'}], caption='Ensaio de compressão')
msg(300, '/diario')
msg(200, '/semana')
msg(200, '/agenda')
mostrar('diário e agenda')

# 5) concluir tarefa
fila_ia.append({'intencao': 'concluir', 'ids_concluidos': ['T001']})
msg(200, 'terminei a molhagem')
mostrar('concluir')

# 6) feed .ics
tok = store.Store().token_agenda('200')
ics = cli.get(f'/agenda/{tok}.ics')
assert ics.status_code == 200 and 'BEGIN:VEVENT' in ics.text and 'T001@ecordena' in ics.text, ics.text
assert cli.get('/agenda/naoexiste.ics').status_code == 404
print('\n.ics OK:', ics.text.count('BEGIN:VEVENT'), 'eventos;', ics.headers['content-type'])
print(agenda.link_google({'id': 'T001', 'tarefa': 'Molhagem', 'data': H, 'hora': '14:00', 'responsaveis': 'Davi'})[:160])

# 7) relatório
fila_ia.append('A equipe moldou tijolos, fez ensaio e concluiu a molhagem.')
msg(999, '/relatorio'); bt(999, 'rel:semana'); bt(300, 'rel:semana')
mostrar('relatório')

# 8) mudança na planilha → aviso (substitui o onEdit)
s = store.Store()
fl = next(m for m in s.membros() if m['chat'] == '300')
mem.escrever('Painel do Professor', fl['linha'], 3, [['Registro e fotos']])   # professor mexendo na planilha
print(cli.get('/cron?key=segredo-teste').text)
mem.escrever('Painel do Professor', fl['linha'], 2, [['Removido']])
print(cli.get('/cron?key=segredo-teste').text)
msg(300, '/hoje')
mostrar('avisos do painel')

# 9) escala com IA
s = store.Store()
s.anexar('Lotes', ['7', datas.add_dias(H, -1), '100', ''])
fila_ia.append({'itens': [{'demanda_id': 'M-7-2', 'data': H, 'hora': '14:00', 'responsaveis': ['Davi Sales', 'Fulano'], 'justificativa': 'disponível'}]})
msg(999, '/escala')
bt(999, 'esc:ok')
mostrar('escala')

print('\nABAS:')
for aba in ['Painel do Professor', 'Tarefas', 'Delegações', 'Registros', 'Proposta']:
    print('--', aba)
    for r in mem.valores(aba)[:12]:
        print('   ', [x for x in r if x][:9])

# 10) salva um relatório de exemplo pra conferir o DOCX
titulo, resumo, conteudo, nome = relatorio.gerar(store.Store(), 'semana')
open(os.path.join(os.path.dirname(__file__), 'exemplo_relatorio.docx'), 'wb').write(conteudo)
print('DOCX:', nome, len(conteudo), 'bytes')
