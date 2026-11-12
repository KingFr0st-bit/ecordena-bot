"""Camada de IA: Groq primeiro (rápido e estável), Gemini de reserva.

A IA só propõe. Tudo o que ela devolve é conferido pelo código antes de ir pra planilha.
"""
import base64
import json
import logging
import re
import time

import requests

from .config import cfg

log = logging.getLogger('ecordena')
_modelo_groq = {'id': None}
_modelo_gemini = {'id': None}

PREFERIDOS_GROQ = ['openai/gpt-oss-120b', 'llama-3.3-70b-versatile', 'openai/gpt-oss-20b', 'llama-3.1-8b-instant']


def _json(texto):
    texto = re.sub(r'^\s*```(json)?', '', str(texto), flags=re.I)
    texto = re.sub(r'```\s*$', '', texto).strip()
    return json.loads(texto)


def ia(prompt, como_json=False, audio=None):
    """audio = (bytes, mime_type) ou None. Devolve texto ou dict."""
    ordem = [n for n, chave in (('groq', cfg.groq), ('gemini', cfg.gemini)) if chave]
    if not ordem:
        raise RuntimeError('Nenhuma chave de IA configurada (GROQ_KEY ou GEMINI_KEY).')
    erros = []
    for nome in ordem:
        try:
            if nome == 'groq':
                return groq(prompt, como_json, audio)
            return gemini(prompt, como_json, audio, limite=1 if cfg.groq else 3)
        except Exception as e:  # noqa: BLE001 — qualquer falha passa pra próxima IA
            erros.append(f'{nome}: {str(e)[:200]}')
            log.warning('IA %s falhou: %s', nome, e)
    raise RuntimeError('Todas as IAs falharam → ' + ' | '.join(erros))


# ------------------------------- Groq -------------------------------

def _cab_groq():
    return {'Authorization': 'Bearer ' + cfg.groq}


def escolher_modelo_groq(excluir=None):
    r = requests.get('https://api.groq.com/openai/v1/models', headers=_cab_groq(), timeout=20)
    r.raise_for_status()
    ids = [m['id'] for m in r.json().get('data', []) if m['id'] != excluir]
    escolhido = next((p for p in PREFERIDOS_GROQ if p in ids), None) or next(
        (i for i in ids if not re.search(r'whisper|guard|tts|orpheus|playai|prompt|compound', i, re.I)), None)
    if not escolhido:
        raise RuntimeError('Nenhum modelo de texto disponível no Groq.')
    _modelo_groq['id'] = escolhido
    return escolhido


def transcrever(audio):
    dados, mime = audio
    for modelo in ('whisper-large-v3-turbo', 'whisper-large-v3'):
        r = requests.post('https://api.groq.com/openai/v1/audio/transcriptions', headers=_cab_groq(),
                          files={'file': ('audio.ogg', dados, mime or 'audio/ogg')},
                          data={'model': modelo, 'language': 'pt', 'response_format': 'json'}, timeout=60)
        if r.status_code == 200:
            return str(r.json().get('text', '')).strip()
    raise RuntimeError('Não consegui transcrever o áudio: ' + r.text[:200])


def groq(prompt, como_json=False, audio=None, ja_trocou=False):
    texto = prompt
    if audio:
        texto += '\n\nTranscrição automática do áudio: "' + transcrever(audio) + '"'
    modelo = cfg.modelo_groq or _modelo_groq['id'] or escolher_modelo_groq()
    corpo = {'model': modelo, 'messages': [{'role': 'user', 'content': texto}], 'temperature': 0.2}
    if como_json:
        corpo['response_format'] = {'type': 'json_object'}
    r = requests.post('https://api.groq.com/openai/v1/chat/completions', headers=_cab_groq(), json=corpo, timeout=60)
    if r.status_code in (400, 404) and not ja_trocou and 'model' in r.text.lower():
        escolher_modelo_groq(modelo)       # modelo aposentado: escolhe outro
        return groq(prompt, como_json, audio, True)
    if r.status_code == 429 and not ja_trocou:
        time.sleep(2)
        return groq(prompt, como_json, audio, True)
    if r.status_code != 200:
        raise RuntimeError(f'Groq respondeu {r.status_code}: {r.text[:300]}')
    conteudo = str(r.json()['choices'][0]['message'].get('content') or '')
    return _json(conteudo) if como_json else conteudo


# ------------------------------ Gemini ------------------------------

def _cab_gemini():
    return {'x-goog-api-key': cfg.gemini}


def escolher_modelo_gemini(excluir=None):
    r = requests.get('https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000',
                     headers=_cab_gemini(), timeout=20)
    r.raise_for_status()

    def versao(n):
        m = re.search(r'gemini-(\d+(?:\.\d+)?)', n)
        return float(m.group(1)) if m else 0

    nomes = [m['name'].replace('models/', '') for m in r.json().get('models', [])
             if 'generateContent' in (m.get('supportedGenerationMethods') or [])]
    cands = [n for n in nomes if n != excluir and (re.match(r'^gemini-[\d.]+-flash(-lite)?$', n)
                                                     or n in ('gemini-flash-latest', 'gemini-flash-lite-latest'))]
    cands.sort(key=lambda n: ('lite' in n, -versao(n)))
    if not cands:
        raise RuntimeError('Nenhum modelo Flash disponível para esta chave.')
    _modelo_gemini['id'] = cands[0]
    return cands[0]


def gemini(prompt, como_json=False, audio=None, tentativa=0, modelo=None, limite=3):
    modelo = modelo or _modelo_gemini['id'] or cfg.modelo_gemini
    partes = [{'text': prompt}]
    if audio:
        partes.append({'inlineData': {'mimeType': audio[1] or 'audio/ogg', 'data': base64.b64encode(audio[0]).decode()}})
    corpo = {'contents': [{'role': 'user', 'parts': partes}], 'generationConfig': {'temperature': 0.2}}
    if como_json:
        corpo['generationConfig']['responseMimeType'] = 'application/json'
    r = requests.post(f'https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent',
                      headers=_cab_gemini(), json=corpo, timeout=90)
    if r.status_code == 404 and tentativa == 0:
        return gemini(prompt, como_json, audio, 1, escolher_modelo_gemini(), limite)
    if r.status_code in (429, 500, 503) and tentativa < limite:
        time.sleep(1.5 * (tentativa + 1))
        try:
            alternativo = escolher_modelo_gemini(modelo)
        except Exception:  # noqa: BLE001
            alternativo = modelo
        return gemini(prompt, como_json, audio, tentativa + 1, alternativo, limite)
    if r.status_code != 200:
        raise RuntimeError(f'Gemini respondeu {r.status_code}: {r.text[:300]}')
    cand = (r.json().get('candidates') or [{}])[0]
    texto = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts', []))
    return _json(texto) if como_json else texto
