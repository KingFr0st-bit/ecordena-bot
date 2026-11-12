"""Servidor web (FastAPI) que o Render roda.

Rotas:
  POST /telegram            webhook do Telegram (protegido por um segredo no cabeçalho)
  GET  /cron?key=...        despertador (cron-job.org a cada 10 min): lembretes, resumo, escala e avisos do painel
  GET  /setup?key=...       prepara a planilha e conecta o bot ao Telegram (rodar uma vez)
  GET  /agenda/<token>.ics  agenda pessoal que o aluno assina no celular
  GET  /                    "ECOrdena online ✅"
"""
import hashlib
import hmac
import logging
import threading
from collections import deque

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from urllib.parse import quote

from . import agenda, datas, logica, tg
from .config import cfg
from .store import CABECALHOS, Store, norm

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('ecordena')
app = FastAPI(title='ECOrdena Bot', docs_url=None, redoc_url=None)

_trava = threading.Lock()          # uma mensagem por vez: evita duas escritas na mesma linha da planilha
_vistos = deque(maxlen=2000)       # o Telegram às vezes entrega a mesma atualização duas vezes


def segredo_telegram():
    """O Telegram só aceita A-Z, a-z, 0-9, _ e - no segredo; derivamos um a partir do APP_SECRET."""
    return hashlib.sha256(('telegram:' + cfg.segredo).encode()).hexdigest()


def _confere(chave):
    if not cfg.segredo or not hmac.compare_digest(str(chave or ''), cfg.segredo):
        raise HTTPException(status_code=403, detail='chave inválida')


def _processar(update):
    with _trava:
        uid = update.get('update_id')
        if uid in _vistos:
            return
        _vistos.append(uid)
        try:
            logica.processar_update(update)
        except Exception:  # noqa: BLE001
            log.exception('erro ao processar update %s', uid)


@app.api_route('/', methods=['GET', 'HEAD'])
@app.api_route('/health', methods=['GET', 'HEAD'])
def online():
    return PlainTextResponse('ECOrdena online ✅')


@app.post('/telegram')
async def webhook(request: Request, tarefas: BackgroundTasks,
                  x_telegram_bot_api_secret_token: str = Header(default='')):
    if not cfg.segredo or not hmac.compare_digest(x_telegram_bot_api_secret_token, segredo_telegram()):
        raise HTTPException(status_code=403)
    update = await request.json()
    tarefas.add_task(_processar, update)   # responde 200 na hora; o Telegram não fica reenviando
    return {'ok': True}


@app.api_route('/cron', methods=['GET', 'HEAD'])
def cron(key: str = ''):
    _confere(key)
    with _trava:
        feito = logica.tick(Store())
    return PlainTextResponse('\n'.join(feito) or 'ok')


@app.get('/setup')
def setup(key: str = ''):
    _confere(key)
    linhas = []
    s = Store()
    for aba in ['Painel do Professor'] + list(CABECALHOS):
        s._vals(aba)                       # cria a aba se ainda não existir
        linhas.append(f'aba ok: {aba}')
    if not s.regras():
        for r in ['Mínimo de 2 pessoas no laboratório em qualquer atividade.',
                  'Molhagem dos tijolos: 1 vez por dia nos 7 primeiros dias após a moldagem, de preferência entre 13h e 17h.',
                  'Só escalar alguém em horário que a pessoa declarou como disponível.',
                  'Cada membro deve ter no máximo 3 tarefas por semana.',
                  'Prensagem exige pelo menos 1 pessoa que já operou a prensa manual.']:
            s.anexar('Regras', [r])
        linhas.append('regras de exemplo adicionadas')
    if not cfg.url_publica:
        return PlainTextResponse('\n'.join(linhas) + '\n❌ Falta PUBLIC_URL (endereço do serviço no Render).', status_code=500)
    me = tg.api('getMe')
    linhas.append(f"Telegram: @{me['result']['username']} ✅" if me.get('ok') else f"Telegram ❌ {me.get('description')}")
    r = tg.api('setWebhook', {'url': cfg.url_publica.rstrip('/') + '/telegram', 'secret_token': segredo_telegram(),
                              'allowed_updates': ['message', 'callback_query'], 'drop_pending_updates': True})
    linhas.append('Webhook conectado ✅' if r.get('ok') else f"Webhook ❌ {r.get('description')}")
    tg.api('setMyCommands', {'commands': [
        {'command': 'hoje', 'description': 'Suas tarefas de hoje'},
        {'command': 'semana', 'description': 'Suas tarefas dos próximos 7 dias'},
        {'command': 'agenda', 'description': 'Colocar as tarefas na agenda do celular'},
        {'command': 'diario', 'description': 'O que você registrou no diário do lab'},
        {'command': 'horarios', 'description': 'Ver ou mudar sua disponibilidade'},
        {'command': 'relatorio', 'description': '(professor) Relatório com fotos'},
        {'command': 'ajuda', 'description': 'O que o bot sabe fazer'}]})
    for nome, chave in (('Groq', cfg.groq), ('Gemini', cfg.gemini)):
        linhas.append(f'{nome}: chave {"configurada ✅" if chave else "não configurada ⚠️"}')
    return PlainTextResponse('\n'.join(linhas) + '\n\nPronto! Manda /start pro bot no Telegram.')


@app.get('/agenda/{token}/google')
def agenda_google(token: str):
    webcal = f"{cfg.url_publica.rstrip('/').replace('https://', 'webcal://', 1)}/agenda/{token}.ics"
    return RedirectResponse('https://calendar.google.com/calendar/render?cid=' + quote(webcal, safe=''))


@app.get('/agenda/{token}/iphone')
def agenda_iphone(token: str):
    return RedirectResponse(f"{cfg.url_publica.rstrip('/').replace('https://', 'webcal://', 1)}/agenda/{token}.ics")


@app.get('/agenda/{token}.ics')
def agenda_ics(token: str):
    s = Store()
    chat = s.chat_do_token(token)
    m = s.membro_por_chat(chat) if chat else None
    if not m or not m['ativo']:
        raise HTTPException(status_code=404)
    limite = datas.add_dias(datas.hoje(), -30)
    tarefas = [t for t in s.tarefas() if t['data'] and t['data'] >= limite and t['status'] != 'Cancelada'
               and (m['professor'] or norm(m['nome']) in [norm(x) for x in t['responsaveis'].split(',')])]
    return Response(agenda.feed_ics(m['nome'], tarefas), media_type='text/calendar; charset=utf-8',
                    headers={'Cache-Control': 'no-cache'})
