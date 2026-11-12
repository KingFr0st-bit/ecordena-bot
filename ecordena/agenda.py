"""Agenda pessoal: link 'Adicionar ao Google Agenda' por tarefa e feed .ics que o aluno assina uma vez."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from . import datas
from .config import TZ

DURACAO = timedelta(hours=1)


def _inicio(t):
    """datetime com fuso (tarefa com hora) ou None (dia inteiro)."""
    if not t['hora']:
        return None
    return datetime.fromisoformat(f"{t['data']}T{t['hora']}:00").replace(tzinfo=datas.FUSO)


def link_google(t, extra=''):
    """Abre o Google Agenda já preenchido. Funciona no celular e no computador."""
    ini = _inicio(t)
    if ini:
        fmt = '%Y%m%dT%H%M%S'
        dates = f"{ini.strftime(fmt)}/{(ini + DURACAO).strftime(fmt)}"
    else:
        d = t['data'].replace('-', '')
        dates = f"{d}/{datas.add_dias(t['data'], 1).replace('-', '')}"
    detalhes = f"Tarefa do lab Reutilizar ECOnstruir [{t['id']}]"
    if t.get('responsaveis'):
        detalhes += f"\nResponsáveis: {t['responsaveis']}"
    if extra:
        detalhes += '\n' + extra
    q = {'action': 'TEMPLATE', 'text': f"🧱 {t['tarefa']}", 'dates': dates, 'ctz': TZ,
         'details': detalhes, 'location': 'Laboratório Reutilizar ECOnstruir - UFRJ Macaé'}
    return 'https://calendar.google.com/calendar/render?' + urlencode(q)


def botoes(tarefas, max_botoes=8):
    """Teclado com um botão de link por tarefa (o Telegram abre o navegador)."""
    bts = [{'text': f"📅 {t['id']} na agenda", 'url': link_google(t)} for t in tarefas[:max_botoes] if t.get('data')]
    return [bts[i:i + 2] for i in range(0, len(bts), 2)] or None


def _esc(s):
    return str(s).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def _dobrar(linha):
    """Linhas do .ics têm no máximo 75 bytes (RFC 5545)."""
    out, atual = [], ''
    for c in linha:
        if len((atual + c).encode()) > 74:
            out.append(atual)
            atual = ' ' + c
        else:
            atual += c
    out.append(atual)
    return '\r\n'.join(out)


def feed_ics(nome, tarefas):
    agora = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    ls = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//ECOrdena//Lab Reutilizar ECOnstruir//PT', 'CALSCALE:GREGORIAN',
          'METHOD:PUBLISH', f'X-WR-CALNAME:{_esc("Lab ECOnstruir · " + nome)}', f'X-WR-TIMEZONE:{TZ}',
          'REFRESH-INTERVAL;VALUE=DURATION:PT1H', 'X-PUBLISHED-TTL:PT1H']
    for t in tarefas:
        if not t.get('data'):
            continue
        ini = _inicio(t)
        ls += ['BEGIN:VEVENT', f"UID:{t['id']}@ecordena", f'DTSTAMP:{agora}']
        if ini:
            u = ini.astimezone(timezone.utc)
            ls += [f"DTSTART:{u.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{(u + DURACAO).strftime('%Y%m%dT%H%M%SZ')}"]
        else:
            ls += [f"DTSTART;VALUE=DATE:{t['data'].replace('-', '')}",
                   f"DTEND;VALUE=DATE:{datas.add_dias(t['data'], 1).replace('-', '')}"]
        descricao = f"[{t['id']}] Responsáveis: {t.get('responsaveis', '')}"
        ls += [f"SUMMARY:{_esc('🧱 ' + t['tarefa'])}",
               f'DESCRIPTION:{_esc(descricao)}',
               'LOCATION:' + _esc('Laboratório Reutilizar ECOnstruir - UFRJ Macaé'),
               'STATUS:' + ('CANCELLED' if t.get('status') == 'Cancelada' else 'CONFIRMED'),
               'BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:Lembrete do lab', 'TRIGGER:-PT1H', 'END:VALARM',
               'END:VEVENT']
    ls.append('END:VCALENDAR')
    return '\r\n'.join(_dobrar(x) for x in ls) + '\r\n'
