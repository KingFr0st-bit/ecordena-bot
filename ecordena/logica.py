"""Regras do bot: cadastro, conversa com a IA, tarefas, delegação, diário, relatórios, escala e lembretes."""
import json
import logging
import re

from . import agenda, datas, relatorio, tg
from .config import DIAS_MOLHAGEM, PESSOAS_POR_MOLHAGEM, cfg
from .ia import ia
from .store import ABA_PAINEL, Store, norm

log = logging.getLogger('ecordena')

AJUDA = '\n'.join([
    'Comandos:',
    '/hoje: suas tarefas de hoje (e atrasadas)',
    '/semana: suas tarefas dos próximos 7 dias',
    '/horarios: ver ou atualizar quando você pode ir ao lab',
    '/diario: o que você já registrou nos últimos dias',
    '/agenda: colocar suas tarefas na agenda do celular',
    '/ajuda: esta mensagem',
    '',
    'Ou fala normal comigo, por texto ou áudio 🎙️',
    'Ex.: "o que eu faço hoje?", "terminei a molhagem do lote 5",',
    '"tô livre terça e quinta das 13h às 17h"',
    'ou "hoje eu peneirei o solo e limpei a prensa" (vai pro diário do lab).',
    '📷 Manda fotos do que fez (com legenda, se quiser) que eu guardo no diário.',
])

AJUDA_PROF = '\n'.join([
    'Comandos de professor:',
    '/alunos: equipe, status e funções',
    '/cadastros: pedidos de alunos esperando aprovação',
    '/escala: gerar a escala da semana agora',
    '/escala_aprovar: aprovar a escala proposta',
    '/pendentes: tarefas em aberto',
    '/relatorio: relatório do dia, semana ou mês (DOCX com fotos)',
    '/agenda: tarefas da equipe na sua agenda',
    '',
    '🗣️ Pra delegar, é só escrever (ou mandar áudio):',
    '• "Davi faz a molhagem amanhã, Flávio fotografa os tijolos"',
    '• "preciso que alguém destorroe o solo até sexta"',
    '  (aí eu escolho quem combina mais e pergunto se a pessoa sabe fazer)',
    '',
    'Na planilha, aba "Painel do Professor":',
    '• retirar aluno: Status = Removido',
    '• mudar função: lista da coluna Função',
    '• criar função: escreva na tabela Funções',
    '• aprovar professor: tabela Professores, Status = Aprovado',
])


# ================================ utilidades ================================

def pertence(t, nome):
    return norm(nome) in [norm(x) for x in t['responsaveis'].split(',')]


def ordem(t):
    return t['data'] + t['hora']


def pendentes_do_membro(s, nome, ate):
    return sorted([t for t in s.tarefas() if t['status'] == 'Pendente' and t['data'] and t['data'] <= ate and pertence(t, nome)],
                  key=ordem)


def texto_tarefas(lista, titulo, nome):
    if not lista:
        return titulo + '\nNada por aqui 🎉'
    h = datas.hoje()
    linhas = []
    for t in lista:
        outros = [x.strip() for x in t['responsaveis'].split(',') if x.strip() and norm(x) != norm(nome)]
        linhas.append(f"• {datas.dia_semana(t['data'])[:3]} {datas.data_curta(t['data'])} {t['hora']} · {t['tarefa']}"
                      + (f" (com {', '.join(outros)})" if outros else '') + f" [{t['id']}]"
                      + (' ⚠️ atrasada' if t['data'] < h else ''))
    return titulo + '\n' + '\n'.join(linhas)


def quando(prazo, hora=''):
    return f"{datas.dia_semana(prazo)[:3]} {datas.data_curta(prazo)}{' ' + hora if hora else ''}"


def avisar_professores(s, texto, teclado=None, exceto=None):
    for c in s.professores_chats():
        if c != exceto:
            tg.enviar(c, texto, teclado)


def tarefa_ficticia(tid, desc, prazo, hora, resp):
    return {'id': tid, 'tarefa': desc, 'data': prazo, 'hora': hora or '', 'responsaveis': resp}


# ================================= entrada =================================

def processar_update(update):
    s = Store()
    if update.get('message'):
        processar_mensagem(s, update['message'])
    elif update.get('callback_query'):
        processar_botao(s, update['callback_query'])


def processar_mensagem(s, msg):
    if (msg.get('chat') or {}).get('type') != 'private':
        return
    chat = str(msg['chat']['id'])
    texto = str(msg.get('text') or msg.get('caption') or '').strip()

    def cmd(nome):
        return re.match(r'^/' + nome + r'(@\w+)?(\s|$)', texto, re.I) is not None

    if cmd('cancelar'):
        s.remover('cad:' + chat)
        return tg.enviar(chat, 'Beleza, cancelei. Manda /start quando quiser.')

    membro = s.membro_por_chat(chat)

    if not membro:   # ainda não cadastrado
        estado = s.get('cad:' + chat)
        if cmd('registrar'):
            return perguntar_tipo(s, chat, re.sub(r'^/registrar(@\w+)?', '', texto, flags=re.I).strip())
        if estado and not texto.startswith('/'):
            return continuar_cadastro(s, chat, texto, json.loads(estado))
        return perguntar_tipo(s, chat, '')

    if membro['status'] == 'pendente':
        return tg.enviar(chat, '⏳ Seu acesso de professor ainda precisa ser aprovado na planilha (aba "Painel do Professor"). '
                               'Assim que aprovarem, eu te aviso aqui.' if membro['professor'] else
                         '⏳ Seu cadastro tá esperando a aprovação do professor. Assim que ele aprovar, eu te aviso aqui.')
    if membro['status'] == 'removido':
        return tg.enviar(chat, 'Você não faz mais parte da equipe no ECOrdena. Se achar que foi engano, fala com o professor.')
    if membro['status'] == 'recusado':
        return tg.enviar(chat, 'Seu cadastro não foi aprovado. Se achar que foi engano, fala com o professor pessoalmente 🙂')

    if cmd('start') or cmd('ajuda') or cmd('registrar'):
        return tg.enviar(chat, f"E aí, {membro['nome']}! Eu sou o ECOrdena Bot, assistente do lab do Reutilizar ECOnstruir 🧱"
                         + (' (você está como professor)\n\n' + AJUDA_PROF if membro['professor'] else
                            (f"\nSua função: {membro['funcao']}" if membro['funcao'] else '') + '\n\n' + AJUDA))

    h = datas.hoje()
    resposta, teclado = None, None
    try:
        prof = membro['professor']
        if prof and cmd('alunos'):
            alunos = [m for m in s.membros() if m['tipo'] == 'aluno']
            icone = {'aprovado': '✅', 'pendente': '⏳', 'recusado': '❌', 'removido': '🚫'}
            resposta = ('👩‍🎓 Equipe do lab:\n' + '\n'.join(
                f"{icone.get(m['status'], '•')} {m['nome']} · {m['funcao'] or 'sem função'}" + ('' if m['chat'] else ' (sem Telegram)')
                for m in alunos) + '\n\nPra mudar status ou função: aba "Painel do Professor".') if alunos else 'Ainda não tem aluno cadastrado.'
        elif prof and cmd('cadastros'):
            pend = [m for m in s.membros() if m['tipo'] == 'aluno' and m['status'] == 'pendente' and m['chat']]
            for m in pend:
                pedir_aprovacao(s, m, chat)
            resposta = f'Reenviei {len(pend)} pedido(s) de cadastro aqui em cima 👆' if pend else 'Nenhum cadastro de aluno esperando aprovação 🎉'
        elif prof and cmd('escala_aprovar'):
            r = aprovar_proposta(s)
            resposta = (f"✅ Escala aprovada! {r['total']} tarefa(s) e {r['avisados']} membro(s) avisados." if r['total']
                        else 'Não tem proposta pra aprovar. Manda /escala pra gerar uma.')
        elif prof and cmd('escala'):
            tg.enviar(chat, '🧠 Gerando a escala da semana com a IA… leva uns segundos.')
            resposta = gerar_proposta_escala(s) or 'Pronto! A proposta tá aqui em cima 👆'
        elif prof and cmd('pendentes'):
            lista = sorted([t for t in s.tarefas() if t['status'] == 'Pendente' and t['data'] and t['data'] <= h], key=ordem)
            resposta = ('📌 Pendentes até hoje:\n' + '\n'.join(
                f"• {datas.data_curta(t['data'])} {t['hora']} · {t['tarefa']} ({t['responsaveis']}) [{t['id']}]" for t in lista)
                if lista else 'Nada pendente até hoje 🎉')
        elif prof and cmd('relatorio'):
            tg.enviar(chat, '🧾 Relatório de qual período?', [[
                {'text': '📅 Hoje', 'callback_data': 'rel:dia'},
                {'text': '🗓️ Semana', 'callback_data': 'rel:semana'},
                {'text': '📆 Mês', 'callback_data': 'rel:mes'}]])
        elif msg.get('photo'):
            resposta = salvar_foto(s, membro, msg)
        elif cmd('diario') or cmd('diário'):
            meus = [r for r in s.registros() if norm(r['nome']) == norm(membro['nome'])][-10:]
            resposta = ('📝 Seus últimos registros no diário:\n' + '\n'.join(
                f"• {datas.data_curta(r['data'])} · {r['atividade']}" + (f" (📷 {len(r['fotos'])})" if r['fotos'] else '')
                for r in meus) if meus else
                'Você ainda não registrou nada no diário. Me conta o que fez hoje (texto ou áudio) e manda fotos 📷')
        elif cmd('agenda'):
            resposta, teclado = texto_agenda(s, membro)
        elif cmd('hoje'):
            lista = pendentes_do_membro(s, membro['nome'], h)
            resposta = texto_tarefas(lista, f"📋 Suas tarefas de hoje ({datas.dia_semana(h)}, {datas.data_curta(h)}):", membro['nome'])
            teclado = agenda.botoes(lista)
        elif cmd('semana'):
            lista = pendentes_do_membro(s, membro['nome'], datas.add_dias(h, 6))
            resposta = texto_tarefas(lista, '🗓️ Suas tarefas dos próximos 7 dias:', membro['nome'])
            teclado = agenda.botoes(lista)
        elif cmd('horarios') or cmd('horários'):
            pedido = re.sub(r'^/hor[aá]rios(@\w+)?', '', texto, flags=re.I).strip()
            resposta = (interpretar(s, membro, 'Minha disponibilidade: ' + pedido, None)[0] if pedido else
                        f"🕒 Sua disponibilidade hoje na planilha:\n{membro['disponibilidade'] or '(vazia)'}\n\n"
                        'Pra mudar, me manda por texto ou áudio, tipo:\n"tô livre terça e quinta das 13h às 17h e sexta de manhã"')
        else:
            voz = msg.get('voice') or msg.get('audio')
            audio = (tg.baixar(voz['file_id']), voz.get('mime_type') or 'audio/ogg') if voz else None
            if not texto and not audio:
                resposta = 'Por enquanto eu entendo texto, áudio e foto 🙂'
            elif prof:
                resposta = interpretar_professor(s, membro, texto, audio)
            else:
                resposta, teclado = interpretar(s, membro, texto, audio)
    except Exception:  # noqa: BLE001
        log.exception('erro ao responder')
        resposta, teclado = 'Deu ruim aqui do meu lado 😵 Tenta de novo daqui a pouco ou manda /hoje.', None
    if resposta:
        tg.enviar(chat, resposta, teclado)
    s.log(chat, membro['nome'], texto or ('[foto]' if msg.get('photo') else '[áudio]'), resposta or '')


# ================================= cadastro =================================

def perguntar_tipo(s, chat, nome):
    s.put('cad:' + chat, json.dumps({'etapa': 'tipo', 'nome': nome or ''}), minutos=60)
    return tg.enviar(chat, 'E aí! Eu sou o ECOrdena Bot, assistente do lab do Reutilizar ECOnstruir 🧱\n\n'
                           'Vamos fazer seu cadastro rapidinho.\n👉 Você é aluno ou professor?', [[
                               {'text': '🎓 Sou aluno', 'callback_data': 'tipo:aluno'},
                               {'text': '👨‍🏫 Sou professor', 'callback_data': 'tipo:prof'}]])


def continuar_cadastro(s, chat, texto, est):
    if not texto:
        return tg.enviar(chat, 'Nessa parte do cadastro eu preciso de texto ✍️')
    if est['etapa'] == 'tipo':
        if re.search(r'prof', texto, re.I):
            est['tipo'] = 'prof'
        elif re.search(r'alun|estud', texto, re.I):
            est['tipo'] = 'aluno'
        else:
            return perguntar_tipo(s, chat, est.get('nome'))
        return depois_do_tipo(s, chat, est)
    if est['etapa'] == 'nome':
        est['nome'] = texto
        return seguir_com_nome(s, chat, est)
    # etapa 'horario' (só aluno)
    disp = normalizar_disponibilidade(texto)
    s.novo_aluno(est['nome'], disp, chat)
    s.remover('cad:' + chat)
    novo = s.membro_por_chat(chat)
    if novo:
        pedir_aprovacao(s, novo)
    return tg.enviar(chat, f"Show! Anotei:\n👤 {est['nome']}\n🕒 {disp}\n\n⏳ Agora é só esperar o professor aprovar. "
                           'Eu te aviso aqui assim que ele aprovar.')


def depois_do_tipo(s, chat, est):
    if est.get('nome'):
        return seguir_com_nome(s, chat, est)
    est['etapa'] = 'nome'
    s.put('cad:' + chat, json.dumps(est), minutos=60)
    return tg.enviar(chat, f"{'👨‍🏫 Professor' if est['tipo'] == 'prof' else '🎓 Aluno'}, beleza!\n👉 Qual é o seu nome? (nome e sobrenome)")


MSG_PROF_PENDENTE = ('Anotado, {nome}! 👨‍🏫\n\n⏳ Por segurança, o acesso de professor precisa ser aprovado direto na planilha '
                     'do lab (aba "Painel do Professor"). Assim que aprovarem, eu te aviso aqui.')
AVISO_PROF = ('🆕 {nome} pediu acesso como PROFESSOR.\nPor segurança, professor só é aprovado na planilha: '
              'aba "Painel do Professor" → tabela Professores → Status = Aprovado.')


def seguir_com_nome(s, chat, est):
    nome = re.sub(r'\s+', ' ', str(est['nome'])).strip()[:60]
    tipo = 'prof' if est.get('tipo') == 'prof' else 'aluno'
    existente = next((m for m in s.membros() if m['tipo'] == tipo and norm(m['nome']) == norm(nome)), None)
    if existente:
        if existente['chat'] and existente['chat'] != chat:
            s.remover('cad:' + chat)
            return tg.enviar(chat, 'Esse nome já está ligado a outra conta do Telegram. Se for você, pede pra um professor '
                                   'apagar o Telegram ID na planilha.')
        s.set_campo(existente, 'chat', chat)
        s.remover('cad:' + chat)
        if existente['professor']:
            # Segurança: ligar um Telegram a uma linha de professor sempre exige aprovação na planilha.
            s.set_campo(existente, 'status', 'Pendente')
            avisar_professores(s, AVISO_PROF.format(nome=existente['nome']), exceto=chat)
            return tg.enviar(chat, MSG_PROF_PENDENTE.format(nome=existente['nome']))
        if existente['status'] == 'aprovado':
            return tg.enviar(chat, f"Fechou, {existente['nome']}! Achei seu nome na planilha e você já tá registrado ✅\n\n" + AJUDA)
        if existente['status'] == 'pendente':
            return tg.enviar(chat, f"Achei seu cadastro, {existente['nome']} ✅ Falta só a aprovação.")
        return tg.enviar(chat, 'Seu cadastro não está ativo. Se achar que foi engano, fala com o professor.')

    if tipo == 'prof':
        s.novo_professor(nome, chat)
        s.remover('cad:' + chat)
        avisar_professores(s, AVISO_PROF.format(nome=nome))
        return tg.enviar(chat, MSG_PROF_PENDENTE.format(nome=nome))

    est.update(etapa='horario', nome=nome)
    s.put('cad:' + chat, json.dumps(est), minutos=60)
    return tg.enviar(chat, f'Prazer, {nome}! 👋\n👉 Agora me diz: em quais dias e horários você pode ir ao laboratório?\n\n'
                           'Ex.: "terça e quinta das 13h às 17h e sexta de manhã"\n(se errou algo, manda /cancelar e começa de novo)')


def normalizar_disponibilidade(texto):
    try:
        r = ia('Reescreva a disponibilidade abaixo no formato "Ter e qui, 13h às 17h; sex, 8h às 12h" (dias abreviados: '
               'seg, ter, qua, qui, sex, sáb; "manhã" = 8h às 12h, "tarde" = 13h às 17h, "noite" = 18h às 21h, '
               '"semana toda" = seg a sex). Responda só com o texto reescrito, sem comentários.\n\n'
               f'Disponibilidade: "{texto}"')
        limpo = str(r or '').strip().strip('"\'').split('\n')[0][:200]
        return limpo or texto[:200]
    except Exception:  # noqa: BLE001
        return texto[:200]


def pedir_aprovacao(s, m, so_para=None):
    teclado = [[{'text': '✅ Aprovar', 'callback_data': 'ap:' + m['chat']},
                {'text': '❌ Recusar', 'callback_data': 'rc:' + m['chat']}]]
    texto = f"🆕 Pedido de cadastro de aluno\n👤 {m['nome']}\n🕒 {m['disponibilidade'] or '(sem horários)'}"
    if so_para:
        return tg.enviar(so_para, texto, teclado)
    avisar_professores(s, texto, teclado)


def teclado_funcoes(s, chat):
    bts = [{'text': f, 'callback_data': f'fn:{chat}:{i}'} for i, f in enumerate(s.funcoes()[:20])]
    return [bts[i:i + 2] for i in range(0, len(bts), 2)]


# ================================== botões ==================================

def processar_botao(s, cq):
    quem = str((cq.get('from') or {}).get('id'))
    msg = cq.get('message') or {}
    chat_msg, msg_id = (msg.get('chat') or {}).get('id'), msg.get('message_id')
    partes = str(cq.get('data') or '').split(':')
    acao, alvo, extra = (partes + ['', '', ''])[:3]

    def responder(aviso=''):
        tg.responder_botao(cq['id'], aviso)

    def editar(texto, teclado=None):
        tg.editar(chat_msg, msg_id, texto, teclado)

    if acao == 'tipo':   # qualquer pessoa pode tocar
        estado = s.get('cad:' + quem)
        if not estado:
            responder('Esse cadastro expirou')
            return tg.enviar(quem, 'Manda /start pra começar de novo 🙂')
        est = json.loads(estado)
        est['tipo'] = 'prof' if alvo == 'prof' else 'aluno'
        responder()
        editar(f"Você é: {'👨‍🏫 Professor' if est['tipo'] == 'prof' else '🎓 Aluno'}")
        return depois_do_tipo(s, quem, est)

    if acao == 'dl':     # aluno respondendo a uma tarefa oferecida pela IA
        d = next((x for x in s.delegacoes() if x['id'] == alvo), None)
        if not d or d['status'] != 'Aguardando':
            responder('Essa tarefa já foi resolvida')
            return editar('ℹ️ Essa tarefa já foi resolvida.')
        atual = next((m for m in s.membros() if m['tipo'] == 'aluno' and d['posicao'] < len(d['candidatos'])
                      and norm(m['nome']) == norm(d['candidatos'][d['posicao']])), None)
        if not atual or atual['chat'] != quem:
            return responder('Essa pergunta não é pra você 🙂')
        if extra == 's':
            tid = s.criar_tarefa(d['tarefa'], d['prazo'], d['hora'], [atual['nome']], 'Delegada pelo professor (aceita pelo aluno)')
            s.atualizar_delegacao(d, status='Aceita', responsavel=atual['nome'])
            responder('Fechou ✅')
            t = tarefa_ficticia(tid, d['tarefa'], d['prazo'], d['hora'], atual['nome'])
            editar(f"✅ Tarefa {tid} é sua: {d['tarefa']}\n📅 Prazo: {quando(d['prazo'], d['hora'])}\n"
                   'Quando terminar, é só me avisar (texto ou áudio).', agenda.botoes([t]))
            if d['professor']:
                tg.enviar(d['professor'], f"✅ {atual['nome']} pegou a tarefa: {d['tarefa']} [{tid}]")
            return
        responder('Beleza, vou perguntar pra outra pessoa')
        editar(f"🙋 Ok! Passei \"{d['tarefa']}\" pra outra pessoa.")
        return oferecer_proximo(s, d, d['posicao'] + 1)

    if quem not in s.professores_chats():
        return responder('Só professores aprovados podem usar esses botões.')

    if acao == 'rel':
        responder('Gerando…')
        editar('🧾 Gerando o relatório… (pode levar alguns segundos)')
        try:
            titulo, resumo, conteudo, nome = relatorio.gerar(s, alvo)
            editar(f'🧾 Relatório {titulo}\n\n{resumo}')
            tg.enviar_documento(quem, nome, conteudo, f'📄 Relatório {titulo} (com fotos)')
        except Exception:  # noqa: BLE001
            log.exception('erro no relatório')
            editar('😵 Não consegui gerar o relatório agora. Tenta de novo em instantes.')
        return

    if acao in ('ap', 'rc'):
        m = next((x for x in s.membros() if x['tipo'] == 'aluno' and x['chat'] == alvo), None)
        if not m:
            responder('Não achei esse cadastro na planilha.')
            return editar('⚠️ Cadastro não encontrado na planilha (talvez tenha sido apagado).')
        if m['status'] != 'pendente':
            responder('Esse pedido já foi resolvido')
            return editar(f"ℹ️ {m['nome']} já está como: {m['status']}.")
        aprovado = acao == 'ap'
        s.set_campo(m, 'status', 'Aprovado' if aprovado else 'Recusado')
        responder('Aprovado ✅' if aprovado else 'Recusado')
        if not aprovado:
            editar(f"❌ {m['nome']}: cadastro recusado.")
            return tg.enviar(m['chat'], 'Seu cadastro no ECOrdena não foi aprovado pelo professor. Se achar que foi engano, '
                                        'fala com ele pessoalmente 🙂')
        editar(f"✅ {m['nome']} aprovado(a)!\n🕒 {m['disponibilidade']}\n\n👉 Qual vai ser a função dele(a) no lab?",
               teclado_funcoes(s, m['chat']))
        return tg.enviar(m['chat'], f"🎉 Seu cadastro foi aprovado, {m['nome']}! Bem-vindo(a) ao ECOrdena.\n\n" + AJUDA)

    if acao == 'fn':
        m = next((x for x in s.membros() if x['tipo'] == 'aluno' and x['chat'] == alvo), None)
        fs = s.funcoes()
        funcao = fs[int(extra)] if extra.isdigit() and int(extra) < len(fs) else None
        if not m or not funcao:
            return responder('Não consegui salvar a função.')
        s.set_campo(m, 'funcao', funcao)
        responder('Função salva ✅')
        editar(f"✅ {m['nome']} aprovado(a)\n🛠️ Função: {funcao}\n\n(Pra mudar depois, use a coluna Função no Painel do Professor.)")
        return tg.enviar(m['chat'], f'🛠️ O professor te designou como: {funcao}.\nA IA vai levar isso em conta na hora de montar a escala.')

    if acao == 'esc':
        if alvo == 'ok':
            responder('Aprovando…')
            r = aprovar_proposta(s)
            return editar(f"✅ Escala aprovada!\n{r['total']} tarefa(s) criadas e {r['avisados']} aluno(s) avisados no Telegram."
                          if r['total'] else 'A proposta já tinha sido aprovada ou está vazia.')
        responder('Beleza')
        return editar('📝 Ok! Ajusta o que quiser na aba Proposta da planilha e depois manda /escala_aprovar aqui.')
    return responder('')


# ============================= IA: conversa do aluno =============================

def interpretar(s, membro, texto, audio):
    """Devolve (resposta, teclado)."""
    h = datas.hoje()
    pend = [{'id': t['id'], 'data': t['data'], 'hora': t['hora'], 'tarefa': t['tarefa'], 'lote': t['lote'], 'atrasada': t['data'] < h}
            for t in pendentes_do_membro(s, membro['nome'], datas.add_dias(h, 6))]
    regras = s.regras()
    prompt = '\n'.join([
        'Você é o ECOrdena Bot, assistente do laboratório do projeto de extensão Reutilizar ECOnstruir (UFRJ Macaé), que produz tijolos de solo-cimento com EPS.',
        f"Hoje é {h} ({datas.dia_semana(h)}). Quem está falando: {membro['nome']} (aluno"
        + (f", função: {membro['funcao']}" if membro['funcao'] else '') + ').',
        f"Disponibilidade atual dessa pessoa na planilha: \"{membro['disponibilidade'] or '(vazia)'}\"",
        'Tarefas pendentes dessa pessoa (inclui atrasadas):', json.dumps(pend, ensure_ascii=False),
        'Regras do laboratório:', '\n'.join('- ' + r for r in regras),
        ('A mensagem veio em áudio (transcrição abaixo). Use o que a pessoa disse.' + (f' Legenda: "{texto}"' if texto else ''))
        if audio else f'Mensagem: "{texto}"',
        'Classifique a intenção:',
        '- consultar_hoje: quer saber o que fazer hoje',
        '- consultar_semana: quer saber das próximas tarefas',
        '- concluir: está dizendo que terminou uma ou mais tarefas',
        '- duvida: pergunta sobre regra, procedimento ou organização do lab',
        '- disponibilidade: está informando ou mudando os dias e horários em que pode ir ao laboratório',
        '- registrar_atividade: está contando o que fez no lab (hoje, ontem ou na semana) sem ser o fim de uma tarefa da lista',
        '- agenda: quer colocar as tarefas na agenda/calendário do celular',
        '- outro: qualquer outra coisa',
        'Regras para você:',
        '- Em "concluir", coloque em ids_concluidos apenas ids da lista acima que a pessoa claramente disse ter terminado.',
        '- Em "registrar_atividade", escreva em atividade um resumo claro e objetivo do que foi feito (sem gírias), e em data_atividade a data (yyyy-MM-dd) em que foi feito; se não disser, use hoje.',
        '- Em "disponibilidade", escreva em disponibilidade_nova a disponibilidade COMPLETA resultante, no formato "Ter e qui, 13h às 17h; sex, 8h às 12h". Se a pessoa só acrescentar ou tirar algo, parta da disponibilidade atual. "Manhã" = 8h às 12h, "tarde" = 13h às 17h, "noite" = 18h às 21h, "semana toda" = seg a sex. Se não der pra entender, deixe vazio e pergunte na resposta.',
        '- Nunca invente tarefas, horários, pessoas ou procedimentos técnicos.',
        '- Se a dúvida não puder ser respondida com as regras acima, diga isso e marque encaminhar_professor como true.',
        '- Resposta curta, simpática, em português do Brasil.',
        'Responda SOMENTE com um objeto JSON: {"transcricao":"","intencao":"","ids_concluidos":[],"disponibilidade_nova":"","atividade":"","data_atividade":"","encaminhar_professor":false,"resposta":""}',
    ])
    r = ia(prompt, True, audio)
    intencao = str(r.get('intencao') or 'outro')
    transcricao = str(r.get('transcricao') or texto or '')

    # Listagens saem direto da planilha, nunca do texto da IA.
    if intencao == 'consultar_hoje':
        lista = pendentes_do_membro(s, membro['nome'], h)
        return texto_tarefas(lista, f"📋 Suas tarefas de hoje ({datas.dia_semana(h)}, {datas.data_curta(h)}):", membro['nome']), agenda.botoes(lista)
    if intencao == 'consultar_semana':
        lista = pendentes_do_membro(s, membro['nome'], datas.add_dias(h, 6))
        return texto_tarefas(lista, '🗓️ Suas tarefas dos próximos 7 dias:', membro['nome']), agenda.botoes(lista)
    if intencao == 'agenda':
        return texto_agenda(s, membro)
    if intencao == 'concluir':
        feitas = concluir(s, r.get('ids_concluidos') or [], membro, transcricao)
        if not feitas:
            return 'Não consegui identificar qual tarefa você terminou 🤔 Manda /hoje e me diz o código, tipo "terminei a T003".', None
        lista = '\n'.join(f"• {t['tarefa']} [{t['id']}]" for t in feitas)
        for t in feitas:
            s.registrar_atividade(membro, f"Concluiu: {t['tarefa']} [{t['id']}]", h)
        avisar_professores(s, f"✅ {membro['nome']} concluiu:\n{lista}", exceto=membro['chat'])
        return f'✅ Anotado! (também foi pro diário do lab)\n{lista}\n\n📷 Se tiver foto, manda aqui que eu anexo.', None
    if intencao == 'registrar_atividade':
        atividade = str(r.get('atividade') or transcricao).strip()[:500]
        q = min(datas.chave(r.get('data_atividade')) or h, h)
        s.registrar_atividade(membro, atividade, q)
        return f'📝 Registrado no diário do lab ({datas.data_curta(q)}):\n{atividade}\n\n📷 Se tiver foto, manda agora que eu anexo nesse registro.', None
    if intencao == 'disponibilidade':
        nova = str(r.get('disponibilidade_nova') or '').strip()[:300]
        if not nova:
            return r.get('resposta') or 'Não entendi os dias e horários 🤔 Manda assim: "tô livre terça e quinta das 13h às 17h".', None
        s.set_campo(membro, 'disp', nova)
        avisar_professores(s, f"🕒 {membro['nome']} atualizou a disponibilidade:\n{nova}", exceto=membro['chat'])
        return f'✅ Anotado! Sua disponibilidade agora é:\n{nova}\n\nVale a partir da próxima escala. Se algo ficou errado, é só me mandar de novo.', None
    if intencao == 'duvida' and r.get('encaminhar_professor') and s.professores_chats():
        avisar_professores(s, f"❓ Dúvida de {membro['nome']}:\n\"{transcricao}\"", exceto=membro['chat'])
        return (r.get('resposta') or 'Essa eu não sei responder.') + '\n\n(Encaminhei pro professor 📨)', None
    return r.get('resposta') or 'Não entendi muito bem 😅 Manda /ajuda pra ver o que eu sei fazer.', None


def concluir(s, ids, membro, registro):
    alvo = [str(x).strip().upper() for x in ids]
    feitas = []
    for t in s.tarefas():
        if t['id'].upper() in alvo and t['status'] == 'Pendente' and pertence(t, membro['nome']):
            s.concluir(t, membro['nome'], registro)
            feitas.append(t)
    return feitas


def texto_agenda(s, membro):
    tok = s.token_agenda(membro['chat'])
    base = cfg.url_publica.rstrip('/')
    https = f'{base}/agenda/{tok}.ics'
    # O Telegram só aceita links http(s) nos botões: os dois passam por rotas nossas que redirecionam.
    texto = ('📅 Sua agenda do lab\n\n'
             'Assine uma vez e as suas tarefas aparecem sozinhas na agenda do celular:\n\n'
             '🤖 Google Agenda: toque em "Google Agenda" e confirme "Adicionar".\n'
             '🍎 iPhone: toque em "iPhone" e confirme "Assinar".\n'
             'Ou cole este link em qualquer app de agenda (opção "assinar por URL"):\n'
             f'{https}\n\n'
             '⏱️ Agendas assinadas atualizam a cada poucas horas. Pra uma tarefa aparecer na hora, '
             'use o botão "📅 … na agenda" que vem junto das tarefas (em /hoje e /semana).\n'
             '🔒 Esse link é só seu: não compartilhe.')
    return texto, [[{'text': '🤖 Google Agenda', 'url': f'{base}/agenda/{tok}/google'},
                    {'text': '🍎 iPhone', 'url': f'{base}/agenda/{tok}/iphone'}]]


def salvar_foto(s, membro, msg):
    """Guarda o file_id da foto (o Telegram guarda o arquivo) e liga ao diário."""
    file_id = msg['photo'][-1]['file_id']
    legenda = str(msg.get('caption') or '').strip()
    if legenda:
        s.registrar_atividade(membro, legenda[:500], datas.hoje(), [file_id])
        return f'📷 Foto salva no diário com a descrição:\n{legenda}'
    ult = s.ultimo_registro_recente(membro['chat'], 30)
    if ult:
        s.anexar_foto(ult, file_id)
        return '📷 Foto anexada ao seu último registro no diário.'
    s.registrar_atividade(membro, 'Foto enviada (sem descrição)', datas.hoje(), [file_id])
    return '📷 Foto salva no diário!\nDica: manda com legenda (ou me conta o que fez antes) que o registro fica completo.'


# ============================ IA: conversa do professor ============================

def interpretar_professor(s, membro, texto, audio):
    h = datas.hoje()
    alunos = [m for m in s.membros() if m['tipo'] == 'aluno' and m['ativo']]
    desc = dict((f, d) for f, d in s.funcoes_com_descricao())
    carga = {}
    for t in s.tarefas():
        if t['status'] == 'Pendente':
            for n in [norm(x) for x in t['responsaveis'].split(',') if x.strip()]:
                carga[n] = carga.get(n, 0) + 1

    equipe = [{'nome': m['nome'], 'funcao': m['funcao'] or 'Membro', 'o_que_a_funcao_faz': desc.get(m['funcao'], ''),
               'disponibilidade': m['disponibilidade'], 'tarefas_pendentes': carga.get(norm(m['nome']), 0)} for m in alunos]
    prompt = '\n'.join([
        'Você é o ECOrdena Bot, assistente do laboratório do projeto de extensão Reutilizar ECOnstruir (UFRJ Macaé), que produz tijolos de solo-cimento com EPS.',
        f"Quem está falando é o professor {membro['nome']}. Hoje é {h} ({datas.dia_semana(h)}).",
        'EQUIPE (alunos ativos; use exatamente estes nomes):', json.dumps(equipe, ensure_ascii=False),
        ('A mensagem veio em áudio (transcrição abaixo).' + (f' Legenda: "{texto}"' if texto else '')) if audio else f'Mensagem do professor: "{texto}"',
        'Classifique a intenção:',
        '- delegar: o professor quer que uma ou mais tarefas sejam feitas (por alguém que ele indicou ou por quem puder)',
        '- relatorio: pede relatório ou resumo do que a equipe fez (periodo: "dia", "semana" ou "mes")',
        '- outro: qualquer outra coisa',
        'Em "delegar", separe cada tarefa em "tarefas":',
        '  descricao: curta e clara, verbo no infinitivo (ex.: "Destorroar o solo para o lote 6")',
        f'  prazo: yyyy-MM-dd (calcule "amanhã", "sexta" etc. a partir de hoje; se não disser, use {datas.add_dias(h, 2)})',
        '  hora: HH:mm ou vazio',
        '  responsavel: nome EXATO da equipe se o professor disse quem faz; senão vazio',
        '  candidatos: se responsavel vazio, até 4 nomes da equipe em ordem de quem é mais indicado (função, disponibilidade no prazo e menor número de tarefas pendentes)',
        '- Nunca invente nomes fora da equipe.',
        '- Em "outro", responda em "resposta" de forma curta e útil, em português do Brasil.',
        'Responda SOMENTE com um objeto JSON: {"transcricao":"","intencao":"","periodo":"","tarefas":[{"descricao":"","prazo":"","hora":"","responsavel":"","candidatos":[]}],"resposta":""}',
    ])
    r = ia(prompt, True, audio)
    intencao = str(r.get('intencao') or 'outro')

    if intencao == 'relatorio':
        periodo = norm(r.get('periodo'))
        periodo = periodo if periodo in ('dia', 'semana', 'mes') else 'semana'
        titulo, resumo, conteudo, nome = relatorio.gerar(s, periodo)
        tg.enviar_documento(membro['chat'], nome, conteudo, f'📄 Relatório {titulo} (com fotos)')
        return f'🧾 Relatório {titulo}\n\n{resumo}'
    tarefas_ia = r.get('tarefas') if isinstance(r.get('tarefas'), list) else []
    if intencao != 'delegar' or not tarefas_ia:
        return r.get('resposta') or 'Não entendi 🤔 Pra delegar, escreve tipo: "Davi faz a molhagem amanhã" ou "preciso que alguém destorroe o solo até sexta".'

    por_nome = {norm(a['nome']): a for a in alunos}
    linhas = []
    for t in tarefas_ia:
        descricao = str(t.get('descricao') or '').strip()[:300]
        if not descricao:
            continue
        prazo = max(datas.chave(t.get('prazo')) or datas.add_dias(h, 2), h)
        hora = datas.hora(t.get('hora'))
        q = quando(prazo, hora)
        resp = por_nome.get(norm(t.get('responsavel')))
        if resp:
            tid = s.criar_tarefa(descricao, prazo, hora, [resp['nome']], f"Delegada por {membro['nome']}")
            if resp['chat']:
                tg.enviar(resp['chat'], f"📌 {membro['nome']} te passou uma tarefa:\n{descricao}\n📅 Prazo: {q} [{tid}]\n\n"
                                        'Quando terminar, é só me avisar (texto ou áudio).',
                          agenda.botoes([tarefa_ficticia(tid, descricao, prazo, hora, resp['nome'])]))
            linhas.append(f"• {descricao} → {resp['nome']} ✅ [{tid}]" + ('' if resp['chat'] else ' (sem Telegram: avisa pessoalmente)'))
            continue
        # Sem responsável: a IA sugere a ordem; o código completa com o resto da equipe (menos carregados primeiro).
        cands = [por_nome[norm(n)] for n in (t.get('candidatos') or []) if norm(n) in por_nome]
        for a in sorted(alunos, key=lambda a: carga.get(norm(a['nome']), 0)):
            if a not in cands:
                cands.append(a)
        nomes = [a['nome'] for a in cands if a['chat']]
        if not nomes:
            tid = s.criar_tarefa(descricao, prazo, hora, [], f"Delegada por {membro['nome']} (sem responsável)")
            linhas.append(f'• {descricao} → ⚠️ ninguém da equipe está no Telegram [{tid}]')
            continue
        d = s.nova_delegacao(descricao, prazo, hora, nomes, membro['chat'])
        oferecer_proximo(s, d, 0)
        linhas.append(f'• {descricao} ({q}) → perguntando pro(a) {nomes[0]} se sabe fazer 🤔')
    return '🗂️ Delegação feita:\n' + '\n'.join(linhas) + '\n\nTe aviso aqui quando alguém pegar.'


def oferecer_proximo(s, d, pos):
    while pos < len(d['candidatos']):
        m = next((x for x in s.membros() if x['tipo'] == 'aluno' and x['ativo'] and norm(x['nome']) == norm(d['candidatos'][pos])), None)
        if m and m['chat']:
            s.atualizar_delegacao(d, posicao=pos)
            tg.enviar(m['chat'], f"📌 O professor precisa de ajuda com:\n{d['tarefa']}\n📅 Prazo: {quando(d['prazo'], d['hora'])}\n\n"
                                 'Você sabe fazer isso e consegue pegar?', [[
                                     {'text': '✅ Sei e pego', 'callback_data': f"dl:{d['id']}:s"},
                                     {'text': '🙋 Não sei / não posso', 'callback_data': f"dl:{d['id']}:n"}]])
            return
        pos += 1
    s.atualizar_delegacao(d, status='Sem responsável', posicao=pos)
    if d['professor']:
        ex = d['tarefa'][:1].lower() + d['tarefa'][1:]
        tg.enviar(d['professor'], f"⚠️ Ninguém pegou a tarefa: {d['tarefa']}\nPerguntei pra: {', '.join(d['candidatos'])}.\n\n"
                                  f"Que tal delegar direto? Ex.: \"{d['candidatos'][0]} faz {ex}\"")


# ================================ escala com IA ================================

def gerar_proposta_escala(s):
    h = datas.hoje()
    fim = datas.add_dias(h, 6)
    equipe = [m for m in s.membros() if m['tipo'] == 'aluno' and m['ativo']]
    ja_existe = {norm(t['tarefa']) for t in s.tarefas() if t['status'] != 'Cancelada'}

    demandas = []
    for r in s.linhas('Lotes'):
        lote, moldagem = r[0].strip(), datas.chave(r[1])
        if not lote or not moldagem:
            continue
        for n in range(1, DIAS_MOLHAGEM + 1):
            d = datas.add_dias(moldagem, n)
            tarefa = f'Molhagem do lote {lote} (dia {n} de {DIAS_MOLHAGEM})'
            if h <= d <= fim and norm(tarefa) not in ja_existe:
                demandas.append({'id': f'M-{lote}-{n}', 'tarefa': tarefa, 'lote': lote, 'data_fixa': d, 'pessoas': PESSOAS_POR_MOLHAGEM})
    for i, r in enumerate(s.linhas('Demandas')):
        tarefa = r[0].strip()
        if not tarefa or norm(tarefa) in ja_existe:
            continue
        prazo = min(datas.chave(r[1]) or fim, fim)
        demandas.append({'id': f'D-{i + 1}', 'tarefa': tarefa, 'lote': '', 'prazo': prazo, 'pessoas': _int(r[2], 1),
                         'duracao_h': _int(r[3], 1), 'observacoes': r[4]})
    if not demandas:
        avisar_professores(s, '🗓️ Nada novo pra escalar nos próximos 7 dias (sem lotes em cura nem demandas novas).')
        return 'Nada novo pra escalar nos próximos 7 dias. Confira as abas Lotes e Demandas.'

    desc = dict((f, d) for f, d in s.funcoes_com_descricao())
    dias = ', '.join(f'{datas.add_dias(h, i)} ({datas.dia_semana(datas.add_dias(h, i))})' for i in range(7))
    prompt = '\n'.join([
        'Você coordena a escala do laboratório do projeto de extensão Reutilizar ECOnstruir (UFRJ Macaé), que produz tijolos de solo-cimento com EPS.',
        f'Monte a escala de {h} a {fim}. Dias: {dias}.',
        'EQUIPE (use exatamente estes nomes):',
        json.dumps([{'nome': m['nome'], 'funcao': m['funcao'] or 'Membro', 'o_que_a_funcao_faz': desc.get(m['funcao'], ''),
                     'disponibilidade': m['disponibilidade'], 'observacoes': m['obs']} for m in equipe], ensure_ascii=False),
        'REGRAS DO LABORATÓRIO:', '\n'.join('- ' + r for r in s.regras()),
        'DEMANDAS A ALOCAR:', json.dumps(demandas, ensure_ascii=False),
        'Instruções:',
        '- Cada demanda vira exatamente um item, com o mesmo demanda_id.',
        '- Demandas com data_fixa acontecem nessa data. As demais, em qualquer dia até o prazo.',
        '- Escolha um horário (HH:mm) dentro da disponibilidade de todos os responsáveis.',
        '- Distribua a carga de forma equilibrada entre a equipe.',
        '- Respeite as funções: priorize quem tem a função ligada à tarefa, sem sobrecarregar ninguém.',
        '- Se ninguém estiver disponível, deixe responsaveis vazio e explique na justificativa.',
        'Responda SOMENTE com um objeto JSON: {"itens":[{"demanda_id":"","data":"yyyy-MM-dd","hora":"HH:mm","responsaveis":[""],"justificativa":""}]}',
    ])
    resp = ia(prompt, True)
    itens = resp if isinstance(resp, list) else resp.get('itens') or []

    # Conferência determinística: a IA propõe, o código confere.
    nomes = {norm(m['nome']): m['nome'] for m in equipe}
    por_id = {d['id']: d for d in demandas}
    vistos, linhas = set(), []
    for it in itens:
        dem = por_id.get(it.get('demanda_id'))
        if not dem or dem['id'] in vistos:
            continue
        vistos.add(dem['id'])
        alertas = []
        citados = [n for n in (it.get('responsaveis') or []) if str(n).strip()]
        validos = [nomes[norm(n)] for n in citados if norm(n) in nomes]
        if len(validos) != len(citados):
            alertas.append('IA citou nome fora da equipe (removido)')
        data = datas.chave(it.get('data'))
        if dem.get('data_fixa') and data != dem['data_fixa']:
            if data:
                alertas.append('Data corrigida para a data obrigatória')
            data = dem['data_fixa']
        if not data or data < h or data > fim:
            alertas.append('Data fora da semana, revisar')
            data = dem.get('prazo') or h
        if dem.get('prazo') and data > dem['prazo']:
            alertas.append('Depois do prazo, revisar')
        if len(validos) < dem['pessoas']:
            alertas.append(f"Precisa de {dem['pessoas']} pessoa(s), revisar")
        linhas.append([data, datas.hora(it.get('hora')), dem['tarefa'], dem['lote'], ', '.join(validos),
                       str(it.get('justificativa') or ''), '; '.join(alertas)])
    for d in demandas:
        if d['id'] not in vistos:
            linhas.append([d.get('data_fixa') or d.get('prazo'), '', d['tarefa'], d['lote'], '', '',
                           'A IA não alocou esta demanda, preencher manualmente'])
    linhas.sort(key=lambda l: (l[0], l[1]))

    n_antigas = len(s.linhas('Proposta'))
    if n_antigas:
        s.escrever('Proposta', 2, 1, [[''] * 7 for _ in range(n_antigas)])
    s.escrever('Proposta', 2, 1, linhas)
    com_alerta = sum(1 for l in linhas if l[6])
    resumo = '\n'.join(f"• {datas.dia_semana(l[0])[:3]} {datas.data_curta(l[0])} {l[1] or '--:--'} · {l[2]}\n   👥 {l[4] or 'SEM RESPONSÁVEL'}"
                       + (f'\n   ⚠️ {l[6]}' if l[6] else '') for l in linhas)
    avisar_professores(s, f'🧠 Escala proposta pela IA ({datas.data_curta(h)} a {datas.data_curta(fim)})\n\n{resumo}'[:3800]
                       + (f'\n\n⚠️ {com_alerta} item(ns) com alerta.' if com_alerta else ''), [[
                           {'text': '✅ Aprovar escala', 'callback_data': 'esc:ok'},
                           {'text': '📝 Vou ajustar', 'callback_data': 'esc:ajustar'}]])
    return None


def aprovar_proposta(s):
    linhas = [r for r in s.linhas('Proposta') if r[2].strip()]
    if not linhas:
        return {'total': 0, 'avisados': 0}
    prox = s.proximo_id_tarefa()
    q = datas.agora().strftime('%d/%m %H:%M')
    for i, r in enumerate(linhas):
        s.anexar('Tarefas', ['T%03d' % (prox + i), datas.chave(r[0]), datas.hora(r[1]), r[2], r[3], r[4], 'Pendente', '',
                             'Aprovada pelo professor em ' + q])
    s.escrever('Proposta', 2, 1, [[''] * 7 for _ in range(len(s.linhas('Proposta')))])
    h = datas.hoje()
    avisados = 0
    for m in s.membros():
        if m['chat'] and m['tipo'] == 'aluno' and m['ativo']:
            lista = pendentes_do_membro(s, m['nome'], datas.add_dias(h, 6))
            if lista:
                tg.enviar(m['chat'], texto_tarefas(lista, '📅 Escala aprovada! Suas tarefas nos próximos dias:', m['nome']),
                          agenda.botoes(lista))
                avisados += 1
    return {'total': len(linhas), 'avisados': avisados}


# ============================ rotinas do despertador ============================

def lembretes(s):
    h = datas.hoje()
    enviados = 0
    for m in s.membros():
        if m['chat'] and m['tipo'] == 'aluno' and m['ativo']:
            lista = pendentes_do_membro(s, m['nome'], h)
            if lista:
                tg.enviar(m['chat'], texto_tarefas(lista, f"Bom dia, {m['nome']}! ☀️ Suas tarefas de hoje:", m['nome']),
                          agenda.botoes(lista))
                enviados += 1
    atrasadas = sorted([t for t in s.tarefas() if t['status'] == 'Pendente' and t['data'] and t['data'] < h], key=ordem)
    if atrasadas:
        avisar_professores(s, f'⚠️ {len(atrasadas)} tarefa(s) atrasada(s):\n' + '\n'.join(
            f"• {datas.data_curta(t['data'])} · {t['tarefa']} ({t['responsaveis']}) [{t['id']}]" for t in atrasadas))
    return f'lembretes enviados para {enviados} aluno(s)'


def resumo_semanal(s):
    h = datas.hoje()
    ini = datas.add_dias(h, -6)
    todas = s.tarefas()
    semana = [t for t in todas if t['data'] and ini <= t['data'] <= h and t['status'] != 'Cancelada']
    feitas = [t for t in semana if t['status'] == 'Concluída']
    atrasadas = sorted([t for t in todas if t['status'] == 'Pendente' and t['data'] and t['data'] < h], key=ordem)
    carga = {}
    for t in semana:
        for n in [x.strip() for x in t['responsaveis'].split(',') if x.strip()]:
            carga[n] = carga.get(n, 0) + 1
    regs = [r for r in s.registros() if r['data'] and ini <= r['data'] <= h]
    texto = '\n'.join([
        f'📊 Resumo da semana ({datas.data_curta(ini)} a {datas.data_curta(h)})', '',
        f'✅ Concluídas: {len(feitas)} de {len(semana)}',
        f'⚠️ Atrasadas em aberto: {len(atrasadas)}',
        f'📝 Registros no diário: {len(regs)}', '',
        'Carga da semana (pra equilibrar a próxima escala):',
        '\n'.join(f'• {n}: {c}' for n, c in sorted(carga.items(), key=lambda x: -x[1])) or '• sem tarefas',
    ]) + ('\n\nAtrasadas:\n' + '\n'.join(f"• {datas.data_curta(t['data'])} · {t['tarefa']} ({t['responsaveis']}) [{t['id']}]"
                                          for t in atrasadas) if atrasadas else '') + '\n\n🧾 Relatório completo com fotos: /relatorio'
    avisar_professores(s, texto)
    return 'resumo enviado'


def sincronizar_painel(s):
    """Substitui o gatilho onEdit do Apps Script: compara o painel com a última foto e avisa as mudanças."""
    atual = {}
    for m in s.membros():
        if m['chat']:
            atual[f"{m['tipo']}:{m['chat']}"] = {'status': m['status'], 'funcao': m['funcao'], 'nome': m['nome']}
    antes_txt = s.get('snapshot_painel')
    if antes_txt is None:
        s.put('snapshot_painel', json.dumps(atual, ensure_ascii=False))
        return 'primeira foto do painel salva'
    antes = json.loads(antes_txt or '{}')
    avisos = 0
    for k, v in atual.items():
        a = antes.get(k)
        if not a:
            continue
        tipo, chat = k.split(':', 1)
        if v['status'] != a['status']:
            avisos += 1
            if tipo == 'aluno':
                if v['status'] == 'aprovado':
                    tg.enviar(chat, f"🎉 Seu cadastro foi aprovado, {v['nome']}! Bem-vindo(a) ao ECOrdena."
                              + (f"\n🛠️ Função: {v['funcao']}" if v['funcao'] else '') + '\n\n' + AJUDA)
                elif v['status'] == 'removido':
                    tg.enviar(chat, '👋 Você foi retirado(a) da equipe do lab no ECOrdena. Se achar que foi engano, fala com o professor.')
                elif v['status'] == 'recusado':
                    tg.enviar(chat, 'Seu cadastro no ECOrdena não foi aprovado pelo professor. Se achar que foi engano, fala com ele pessoalmente 🙂')
            else:
                if v['status'] == 'aprovado':
                    tg.enviar(chat, f"✅ Seu acesso de professor foi liberado, {v['nome']}!\n\nVocê vai receber aqui os pedidos de "
                                    'cadastro, a escala da semana pra aprovar e as dúvidas que a IA não souber responder.\n\n' + AJUDA_PROF)
                elif v['status'] == 'recusado':
                    tg.enviar(chat, 'Seu pedido de acesso como professor não foi aprovado.')
        elif tipo == 'aluno' and v['funcao'] != a['funcao'] and v['funcao'] and v['status'] == 'aprovado':
            avisos += 1
            tg.enviar(chat, f"🛠️ Sua função no lab agora é: {v['funcao']}.\nA IA vai levar isso em conta na próxima escala.")
    if atual != antes:
        s.put('snapshot_painel', json.dumps(atual, ensure_ascii=False))
    return f'{avisos} aviso(s) de mudança no painel'


def tick(s):
    """Chamado pelo despertador a cada ~10 min. Roda o que estiver na hora, uma vez só."""
    agora = datas.agora()
    h = datas.hoje()
    feito = [sincronizar_painel(s)]
    rotinas = [('lembretes', agora.hour >= 7, lembretes),
               ('resumo', agora.weekday() == 4 and agora.hour >= 17, resumo_semanal),
               ('escala', agora.weekday() == 6 and agora.hour >= 18, gerar_proposta_escala)]
    for nome, na_hora, fn in rotinas:
        if na_hora and s.get('rodou:' + nome) != h:
            s.put('rodou:' + nome, h)       # marca antes, pra nunca rodar duas vezes
            try:
                feito.append(f'{nome}: {fn(s) or "ok"}')
            except Exception:  # noqa: BLE001
                log.exception('rotina %s falhou', nome)
                feito.append(f'{nome}: erro')
    return feito


def _int(v, padrao):
    try:
        return int(float(str(v).replace(',', '.')))
    except ValueError:
        return padrao


__all__ = ['processar_update', 'tick', 'ABA_PAINEL']
