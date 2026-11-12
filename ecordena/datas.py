"""Datas sempre como texto 'yyyy-mm-dd' (chave) no fuso de São Paulo."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import TZ

FUSO = ZoneInfo(TZ)
DIAS = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']


def agora():
    return datetime.now(FUSO)


def hoje():
    return agora().strftime('%Y-%m-%d')


def _d(chave_):
    return date.fromisoformat(chave_)


def add_dias(chave_, n):
    return (_d(chave_) + timedelta(days=n)).isoformat()


def data_curta(chave_):
    if not chave_:
        return '--/--'
    a, m, d = chave_.split('-')
    return f'{d}/{m}'


def dia_semana(chave_):
    return DIAS[_d(chave_).weekday()]


def chave(v):
    """Aceita 'yyyy-mm-dd', 'dd/mm/yyyy', date ou datetime; devolve 'yyyy-mm-dd' ou ''."""
    if not v:
        return ''
    if isinstance(v, datetime):
        return v.astimezone(FUSO).strftime('%Y-%m-%d') if v.tzinfo else v.strftime('%Y-%m-%d')
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    m = re.match(r'^\d{4}-\d{2}-\d{2}', s)
    return m.group(0) if m else ''


def hora(v):
    s = str(v or '').strip()
    m = re.match(r'^(\d{1,2})(?::|h)(\d{2})?', s)
    if not m:
        return ''
    return f'{int(m.group(1)):02d}:{m.group(2) or "00"}'


def carimbo():
    """Data e hora legíveis para colunas como 'Registrado em'."""
    return agora().strftime('%Y-%m-%d %H:%M:%S')


def de_carimbo(s):
    try:
        return datetime.strptime(str(s).strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=FUSO)
    except ValueError:
        return None
