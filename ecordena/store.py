"""Acesso aos dados. A planilha do Google é o banco (o disco do Render grátis é apagado).

Cada atualização do Telegram usa um Store novo: cada aba é lida no máximo uma vez por mensagem.
"""
import json
import secrets
from datetime import timedelta

from . import datas
from .config import cfg

ABA_PAINEL = 'Painel do Professor'
CABECALHOS = {
    'Tarefas': ['ID', 'Data', 'Hora', 'Tarefa', 'Lote', 'Responsáveis', 'Status', 'Concluída em', 'Registro'],
    'Registros': ['Data', 'Nome', 'Atividade', 'Fotos (Telegram)', 'Telegram ID', 'Registrado em'],
    'Delegações': ['ID', 'Tarefa', 'Prazo', 'Hora', 'Candidatos', 'Posição', 'Status', 'Responsável', 'Professor', 'Criada em'],
    'Lotes': ['Lote', 'Data de moldagem', 'Qtd. de tijolos', 'Observações'],
    'Demandas': ['Tarefa', 'Prazo', 'Pessoas necessárias', 'Duração (h)', 'Observações'],
    'Regras': ['Regra'],
    'Proposta': ['Data', 'Hora', 'Tarefa', 'Lote', 'Responsáveis', 'Justificativa da IA', 'Alerta'],
    'Log': ['Quando', 'Chat ID', 'Nome', 'Mensagem', 'Resposta'],
    '_estado': ['Chave', 'Valor', 'Expira'],
}

# Painel do Professor: três tabelas lado a lado, dados a partir da linha 3.
INI = 3
ALU = {'nome': 1, 'status': 2, 'funcao': 3, 'disp': 4, 'obs': 5, 'chat': 6}   # A–F
FUN = {'nome': 8, 'desc': 9}                                                # H–I
PRO = {'nome': 11, 'status': 12, 'chat': 13}                                # K–M
STATUS_ALUNO = ['Aprovado', 'Pendente', 'Recusado', 'Removido']
STATUS_PROF = ['Aprovado', 'Pendente', 'Recusado']
FUNCOES_PADRAO = [
    ['Membro', 'Participa das atividades gerais do lab'],
    ['Operador da prensa', 'Opera a prensa manual na moldagem dos tijolos'],
    ['Responsável pela molhagem', 'Garante a molhagem diária nos 7 primeiros dias de cura'],
    ['Responsável pelo solo', 'Destorroamento e preparo do solo'],
    ['Registro e fotos', 'Fotografa, cataloga e registra no diário do lab'],
    ['Coordenador de turno', 'Organiza a equipe presente no turno'],
]


def norm(s):
    import unicodedata
    s = unicodedata.normalize('NFD', str(s or ''))
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower().strip()


# ------------------------------ backends ------------------------------

class SheetsBackend:
    """Google Sheets via conta de serviço (gspread)."""

    def __init__(self):
        import gspread
        cred = json.loads(cfg.credenciais)
        self.ss = gspread.service_account_from_dict(cred).open_by_key(cfg.planilha)
        self._abas = {}

    def _ws(self, aba):
        if aba not in self._abas:
            self._abas[aba] = self.ss.worksheet(aba)
        return self._abas[aba]

    def existe(self, aba):
        try:
            self._ws(aba)
            return True
        except Exception:  # noqa: BLE001 — WorksheetNotFound
            return False

    def criar(self, aba, linhas=1000, colunas=26, indice=None):
        ws = self.ss.add_worksheet(title=aba, rows=linhas, cols=colunas, index=indice)
        self._abas[aba] = ws
        return ws

    def valores(self, aba):
        return self._ws(aba).get_all_values()

    def escrever(self, aba, linha, col, matriz):
        from gspread.utils import rowcol_to_a1
        ini = rowcol_to_a1(linha, col)
        fim = rowcol_to_a1(linha + len(matriz) - 1, col + max(len(r) for r in matriz) - 1)
        self._ws(aba).update(range_name=f'{ini}:{fim}', values=matriz, value_input_option='RAW')

    def anexar(self, aba, linhas):
        self._ws(aba).append_rows(linhas, value_input_option='RAW', insert_data_option='INSERT_ROWS', table_range='A1')

    def limpar(self, aba, linha_ini, n_cols):
        ws = self._ws(aba)
        from gspread.utils import rowcol_to_a1
        ws.batch_clear([f'{rowcol_to_a1(linha_ini, 1)}:{rowcol_to_a1(max(ws.row_count, linha_ini), n_cols)}'])

    def formatar(self, requests_):
        self.ss.batch_update({'requests': requests_})

    def id_aba(self, aba):
        return self._ws(aba).id


class MemoriaBackend:
    """Usado nos testes: mesma interface, tudo na memória."""

    def __init__(self):
        self.abas = {}

    def existe(self, aba):
        return aba in self.abas

    def criar(self, aba, linhas=1000, colunas=26, indice=None):
        self.abas[aba] = []

    def valores(self, aba):
        linhas = [list(r) for r in self.abas[aba]]
        while linhas and not any(str(x).strip() for x in linhas[-1]):
            linhas.pop()
        largura = max((len(r) for r in linhas), default=0)
        return [r + [''] * (largura - len(r)) for r in linhas]

    def escrever(self, aba, linha, col, matriz):
        t = self.abas[aba]
        for i, row in enumerate(matriz):
            while len(t) < linha + i:
                t.append([])
            r = t[linha + i - 1]
            while len(r) < col - 1 + len(row):
                r.append('')
            for j, v in enumerate(row):
                r[col - 1 + j] = '' if v is None else str(v)

    def anexar(self, aba, linhas):
        n = len(self.valores(aba))
        for i, l in enumerate(linhas):
            self.escrever(aba, n + 1 + i, 1, [l])

    def limpar(self, aba, linha_ini, n_cols):
        self.abas[aba] = self.abas[aba][:linha_ini - 1]

    def formatar(self, requests_):
        pass

    def id_aba(self, aba):
        return 0


_backend = {'b': None}


def backend():
    if _backend['b'] is None:
        _backend['b'] = SheetsBackend()
    return _backend['b']


# -------------------------------- Store --------------------------------

class Store:
    def __init__(self, b=None):
        self.b = b or backend()
        self._cache = {}

    # ---- básico ----
    def _vals(self, aba):
        if aba not in self._cache:
            if not self.b.existe(aba):
                self.criar_aba(aba)
            self._cache[aba] = self.b.valores(aba)
        return self._cache[aba]

    def _sujou(self, aba):
        self._cache.pop(aba, None)

    def linhas(self, aba):
        """Linhas de dados (sem o cabeçalho), com largura do cabeçalho."""
        n = len(CABECALHOS[aba])
        return [(r + [''] * n)[:n] for r in self._vals(aba)[1:]]

    def criar_aba(self, aba):
        if aba == ABA_PAINEL:
            return self.montar_painel()
        self.b.criar(aba, colunas=max(len(CABECALHOS[aba]), 5))
        self.b.escrever(aba, 1, 1, [CABECALHOS[aba]])
        try:
            sid = self.b.id_aba(aba)
            self.b.formatar([
                {'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
                                'cell': {'userEnteredFormat': {'textFormat': {'bold': True, 'foregroundColor': _cor('#F4F1EA')},
                                                               'backgroundColor': _cor('#14261F')}},
                                'fields': 'userEnteredFormat(textFormat,backgroundColor)'}},
                {'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'frozenRowCount': 1}},
                                           'fields': 'gridProperties.frozenRowCount'}},
            ])
        except Exception:  # noqa: BLE001 — formatação é só estética
            pass

    def anexar(self, aba, linha):
        """Escreve logo abaixo da última linha preenchida e devolve o número dessa linha."""
        vals = self._vals(aba)
        n = len(vals) + 1
        nova = [('' if v is None else str(v)) for v in linha]
        self.b.escrever(aba, n, 1, [nova])
        vals.append(nova)          # mantém o cache em dia sem reler a aba
        return n

    def escrever(self, aba, linha, col, matriz):
        vals = self._vals(aba)
        matriz = [[('' if v is None else str(v)) for v in r] for r in matriz]
        self.b.escrever(aba, linha, col, matriz)
        for i, row in enumerate(matriz):       # atualiza o cache no lugar (sem reler a aba)
            while len(vals) < linha + i:
                vals.append([])
            r = vals[linha + i - 1]
            while len(r) < col - 1 + len(row):
                r.append('')
            r[col - 1:col - 1 + len(row)] = row

    # ---- painel ----
    def montar_painel(self):
        b = self.b
        b.criar(ABA_PAINEL, linhas=600, colunas=13, indice=0)
        b.escrever(ABA_PAINEL, 1, 1, [['👩‍🎓 ALUNOS', '', '', '', '', '', '', '🛠️ FUNÇÕES', '', '', '👨‍🏫 PROFESSORES', '', '']])
        b.escrever(ABA_PAINEL, 2, 1, [['Nome', 'Status', 'Função', 'Disponibilidade', 'Observações', 'Telegram ID', '',
                                       'Função', 'Descrição', '', 'Nome', 'Status', 'Telegram ID']])
        b.escrever(ABA_PAINEL, INI, FUN['nome'], FUNCOES_PADRAO)
        try:
            sid = b.id_aba(ABA_PAINEL)

            def faixa(c0, c1, r0=INI - 1, r1=600):
                return {'sheetId': sid, 'startRowIndex': r0, 'endRowIndex': r1, 'startColumnIndex': c0 - 1, 'endColumnIndex': c1}

            def lista(rng, valores):
                return {'setDataValidation': {'range': rng, 'rule': {
                    'condition': {'type': 'ONE_OF_LIST', 'values': [{'userEnteredValue': v} for v in valores]},
                    'strict': True, 'showCustomUi': True}}}

            def regra(rng, texto, fundo):
                return {'addConditionalFormatRule': {'index': 0, 'rule': {'ranges': [rng], 'booleanRule': {
                    'condition': {'type': 'TEXT_EQ', 'values': [{'userEnteredValue': texto}]},
                    'format': {'backgroundColor': _cor(fundo)}}}}}

            st, sp = faixa(2, 2), faixa(12, 12)
            cores = {'Aprovado': '#D9EAD3', 'Pendente': '#FCE8B2', 'Recusado': '#F4C7C3', 'Removido': '#E0E0E0'}
            reqs = [
                {'repeatCell': {'range': faixa(1, 13, 0, 1), 'cell': {'userEnteredFormat': {
                    'textFormat': {'bold': True, 'foregroundColor': _cor('#FFFFFF')}, 'backgroundColor': _cor('#2F6B4F')}},
                    'fields': 'userEnteredFormat(textFormat,backgroundColor)'}},
                {'repeatCell': {'range': faixa(1, 13, 1, 2), 'cell': {'userEnteredFormat': {
                    'textFormat': {'bold': True, 'foregroundColor': _cor('#F4F1EA')}, 'backgroundColor': _cor('#14261F')}},
                    'fields': 'userEnteredFormat(textFormat,backgroundColor)'}},
                {'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'frozenRowCount': 2}},
                                           'fields': 'gridProperties.frozenRowCount'}},
                lista(st, STATUS_ALUNO), lista(sp, STATUS_PROF),
                {'setDataValidation': {'range': faixa(3, 3), 'rule': {'condition': {'type': 'ONE_OF_RANGE', 'values': [
                    {'userEnteredValue': f"='{ABA_PAINEL}'!H3:H102"}]}, 'strict': False, 'showCustomUi': True}}},
            ] + [regra(st, k, v) for k, v in cores.items()] + [regra(sp, k, v) for k, v in cores.items() if k != 'Removido']
            for col, larg in [(1, 150), (2, 95), (3, 190), (4, 260), (5, 200), (6, 110), (7, 24), (8, 190), (9, 300),
                              (10, 24), (11, 150), (12, 95), (13, 110)]:
                reqs.append({'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS',
                                                                     'startIndex': col - 1, 'endIndex': col},
                                                           'properties': {'pixelSize': larg}, 'fields': 'pixelSize'}})
            b.formatar(reqs)
        except Exception:  # noqa: BLE001
            pass

    def _painel(self):
        vals = self._vals(ABA_PAINEL)
        return [(r + [''] * 13)[:13] for r in vals[INI - 1:]]

    def membros(self):
        lista = []
        for i, r in enumerate(self._painel()):
            linha = i + INI
            if r[0].strip():
                st = norm(r[1]) or 'aprovado'
                lista.append(dict(tipo='aluno', professor=False, linha=linha, nome=r[0].strip(), status=st,
                                  ativo=st == 'aprovado', funcao=r[2].strip(), disponibilidade=r[3], obs=r[4],
                                  chat=r[5].strip()))
            if r[PRO['nome'] - 1].strip():
                st = norm(r[PRO['status'] - 1]) or 'pendente'
                lista.append(dict(tipo='prof', professor=True, linha=linha, nome=r[PRO['nome'] - 1].strip(), status=st,
                                  ativo=st == 'aprovado', funcao='Professor', disponibilidade='', obs='',
                                  chat=r[PRO['chat'] - 1].strip()))
        return lista

    def membro_por_chat(self, chat):
        return next((m for m in self.membros() if m['chat'] == str(chat)), None)

    def set_campo(self, m, campo, valor):
        mapa = ({'status': ALU['status'], 'funcao': ALU['funcao'], 'disp': ALU['disp'], 'obs': ALU['obs'], 'chat': ALU['chat']}
                if m['tipo'] == 'aluno' else {'status': PRO['status'], 'chat': PRO['chat']})
        if campo in mapa:
            self.escrever(ABA_PAINEL, m['linha'], mapa[campo], [[valor]])
        # O próprio bot já avisou essa mudança: atualiza a "foto" do painel pra o despertador não avisar de novo.
        if campo in ('status', 'funcao') and m['chat']:
            foto_txt = self.get('snapshot_painel')
            if foto_txt:
                foto = json.loads(foto_txt)
                k = f"{m['tipo']}:{m['chat']}"
                if k in foto:
                    foto[k][campo] = norm(valor) if campo == 'status' else valor
                    self.put('snapshot_painel', json.dumps(foto, ensure_ascii=False))

    def _proxima_linha(self, col):
        p = self._painel()
        for i, r in enumerate(p):
            if not r[col - 1].strip():
                return i + INI
        return len(p) + INI

    def novo_aluno(self, nome, disp, chat):
        self.escrever(ABA_PAINEL, self._proxima_linha(ALU['nome']), 1,
                      [[nome, 'Pendente', 'Membro', disp, 'Cadastrado pelo Telegram', str(chat)]])

    def novo_professor(self, nome, chat):
        self.escrever(ABA_PAINEL, self._proxima_linha(PRO['nome']), PRO['nome'], [[nome, 'Pendente', str(chat)]])

    def funcoes_com_descricao(self):
        lista = [[r[FUN['nome'] - 1].strip(), r[FUN['desc'] - 1].strip()] for r in self._painel() if r[FUN['nome'] - 1].strip()]
        return lista or FUNCOES_PADRAO

    def funcoes(self):
        return [f[0] for f in self.funcoes_com_descricao()]

    def professores_chats(self):
        return [m['chat'] for m in self.membros() if m['professor'] and m['ativo'] and m['chat']]

    # ---- tarefas ----
    def tarefas(self):
        out = []
        for i, r in enumerate(self.linhas('Tarefas')):
            if r[0].strip():
                out.append(dict(linha=i + 2, id=r[0].strip(), data=datas.chave(r[1]), hora=datas.hora(r[2]), tarefa=r[3],
                                lote=r[4], responsaveis=r[5], status=r[6].strip() or 'Pendente',
                                concluida_em=datas.chave(r[7])))
        return out

    def proximo_id_tarefa(self):
        n = max([int(''.join(c for c in t['id'] if c.isdigit()) or 0) for t in self.tarefas()] or [0])
        return n + 1

    def criar_tarefa(self, descricao, prazo, hora, responsaveis, registro, lote=''):
        tid = 'T%03d' % self.proximo_id_tarefa()
        self.anexar('Tarefas', [tid, prazo, hora or '', descricao, lote, ', '.join(responsaveis), 'Pendente', '', registro])
        return tid

    def concluir(self, t, quem, registro):
        self.escrever('Tarefas', t['linha'], 7, [['Concluída', datas.carimbo(), f'{quem}: {registro}'[:500]]])

    # ---- diário ----
    def registros(self):
        out = []
        for i, r in enumerate(self.linhas('Registros')):
            if r[1].strip():
                out.append(dict(linha=i + 2, data=datas.chave(r[0]), nome=r[1].strip(), atividade=r[2],
                                fotos=[f for f in r[3].split(',') if f.strip()], chat=r[4].strip(), quando=r[5]))
        return out

    def registrar_atividade(self, membro, atividade, data=None, fotos=None):
        return self.anexar('Registros', [data or datas.hoje(), membro['nome'], atividade, ','.join(fotos or []),
                                         membro['chat'], datas.carimbo()])

    def ultimo_registro_recente(self, chat, minutos=30):
        limite = datas.agora() - timedelta(minutes=minutos)
        for r in reversed(self.registros()):
            if r['chat'] == str(chat):
                q = datas.de_carimbo(r['quando'])
                return r if q and q >= limite else None
        return None

    def anexar_foto(self, reg, file_id):
        fotos = reg['fotos'] + [file_id]
        self.escrever('Registros', reg['linha'], 4, [[','.join(fotos)]])

    # ---- delegações ----
    def delegacoes(self):
        out = []
        for i, r in enumerate(self.linhas('Delegações')):
            if r[0].strip():
                out.append(dict(linha=i + 2, id=r[0].strip(), tarefa=r[1], prazo=datas.chave(r[2]), hora=datas.hora(r[3]),
                                candidatos=[c.strip() for c in r[4].split('|') if c.strip()],
                                posicao=int(r[5] or 0), status=r[6].strip(), responsavel=r[7], professor=r[8].strip()))
        return out

    def nova_delegacao(self, tarefa, prazo, hora, candidatos, professor):
        did = 'D%03d' % (len(self.delegacoes()) + 1)
        linha = self.anexar('Delegações', [did, tarefa, prazo, hora or '', ' | '.join(candidatos), 0, 'Aguardando', '',
                                           professor or '', datas.carimbo()])
        return dict(linha=linha, id=did, tarefa=tarefa, prazo=prazo, hora=hora or '', candidatos=candidatos, posicao=0,
                    status='Aguardando', responsavel='', professor=professor or '')

    def atualizar_delegacao(self, d, **campos):
        col = {'posicao': 6, 'status': 7, 'responsavel': 8}
        for k, v in campos.items():
            self.escrever('Delegações', d['linha'], col[k], [[v]])
            d[k] = v

    # ---- estado (substitui o CacheService do Apps Script) ----
    def _estado(self):
        return self.linhas('_estado')

    def get(self, chave_):
        agora = datas.carimbo()
        for r in self._estado():
            if r[0] == chave_:
                if r[2] and r[2] < agora:
                    return None
                return r[1]
        return None

    def put(self, chave_, valor, minutos=None):
        expira = (datas.agora() + timedelta(minutes=minutos)).strftime('%Y-%m-%d %H:%M:%S') if minutos else ''
        for i, r in enumerate(self._estado()):
            if r[0] == chave_:
                self.escrever('_estado', i + 2, 2, [[valor, expira]])
                return
        self.anexar('_estado', [chave_, valor, expira])

    def remover(self, chave_):
        for i, r in enumerate(self._estado()):
            if r[0] == chave_:
                self.escrever('_estado', i + 2, 2, [['', '2000-01-01 00:00:00']])   # expira já
                return

    # ---- agenda pessoal ----
    def token_agenda(self, chat):
        tok = self.get('agenda:' + str(chat))
        if not tok:
            tok = secrets.token_urlsafe(16)
            self.put('agenda:' + str(chat), tok)
        return tok

    def chat_do_token(self, tok):
        for r in self._estado():
            if r[0].startswith('agenda:') and r[1] == tok:
                return r[0].split(':', 1)[1]
        return None

    # ---- outros ----
    def regras(self):
        return [r[0].strip() for r in self.linhas('Regras') if r[0].strip()]

    def log(self, chat, nome, msg, resp):
        try:
            self.anexar('Log', [datas.carimbo(), str(chat), nome, str(msg)[:1000], str(resp)[:1000]])
        except Exception:  # noqa: BLE001 — log nunca derruba o bot
            pass


def _cor(hexa):
    h = hexa.lstrip('#')
    return {'red': int(h[0:2], 16) / 255, 'green': int(h[2:4], 16) / 255, 'blue': int(h[4:6], 16) / 255}
