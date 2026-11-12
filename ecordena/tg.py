"""Chamadas à API de bots do Telegram."""
import logging

import requests

from .config import cfg

log = logging.getLogger('ecordena')


def api(metodo, payload=None, arquivos=None, timeout=30):
    if not cfg.telegram:
        raise RuntimeError('TELEGRAM_TOKEN não configurado.')
    url = f'https://api.telegram.org/bot{cfg.telegram}/{metodo}'
    if arquivos:
        r = requests.post(url, data=payload or {}, files=arquivos, timeout=120)
    else:
        r = requests.post(url, json=payload or {}, timeout=timeout)
    try:
        return r.json()
    except ValueError:
        return {'ok': False, 'description': f'HTTP {r.status_code}'}


def enviar(chat, texto, teclado=None):
    corpo = {'chat_id': chat, 'text': str(texto)[:4000]}
    if teclado:
        corpo['reply_markup'] = {'inline_keyboard': teclado}
    r = api('sendMessage', corpo)
    if not r.get('ok'):
        log.warning('sendMessage falhou: %s', r.get('description'))
    return r


def editar(chat, message_id, texto, teclado=None):
    corpo = {'chat_id': chat, 'message_id': message_id, 'text': str(texto)[:4000]}
    if teclado:
        corpo['reply_markup'] = {'inline_keyboard': teclado}
    return api('editMessageText', corpo)


def responder_botao(callback_id, aviso=''):
    return api('answerCallbackQuery', {'callback_query_id': callback_id, 'text': aviso or ''})


def baixar(file_id):
    info = api('getFile', {'file_id': file_id})
    if not info.get('ok'):
        raise RuntimeError('Não consegui baixar o arquivo: ' + str(info.get('description')))
    url = f"https://api.telegram.org/file/bot{cfg.telegram}/{info['result']['file_path']}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def enviar_documento(chat, nome, conteudo, legenda=''):
    r = api('sendDocument', {'chat_id': chat, 'caption': legenda[:1000]},
            arquivos={'document': (nome, conteudo)})
    if not r.get('ok'):
        log.warning('sendDocument falhou: %s', r.get('description'))
    return r
