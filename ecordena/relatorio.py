"""Relatório do dia, semana ou mês em DOCX, com as fotos que os alunos mandaram no Telegram."""
import io
import logging

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

from . import datas, tg
from .ia import ia
from .store import norm

log = logging.getLogger('ecordena')
VERDE = RGBColor(0x2F, 0x6B, 0x4F)
MAX_FOTOS = 40


def _foto(file_id):
    """Baixa a foto do Telegram e reduz pra caber leve no documento."""
    from PIL import Image
    img = Image.open(io.BytesIO(tg.baixar(file_id)))
    img = img.convert('RGB')
    img.thumbnail((1200, 1200))
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=80)
    out.seek(0)
    return out


def gerar(store, periodo):
    """Devolve (titulo, resumo_curto, bytes_docx, nome_arquivo)."""
    h = datas.hoje()
    dias = {'dia': 0, 'semana': 6, 'mes': 29}.get(periodo, 6)
    ini = datas.add_dias(h, -dias)
    titulo = f'do dia {datas.data_curta(h)}' if ini == h else f'de {datas.data_curta(ini)} a {datas.data_curta(h)}'

    def dentro(d):
        return bool(d) and ini <= d <= h

    regs = [r for r in store.registros() if dentro(r['data'])]
    todas = store.tarefas()
    concl = [t for t in todas if t['status'] == 'Concluída' and (dentro(t['concluida_em']) or (not t['concluida_em'] and dentro(t['data'])))]
    previstas = [t for t in todas if dentro(t['data']) and t['status'] != 'Cancelada']
    atrasadas = sorted([t for t in todas if t['status'] == 'Pendente' and t['data'] and t['data'] < h],
                       key=lambda t: t['data'] + t['hora'])
    n_fotos = sum(len(r['fotos']) for r in regs)

    resumo_ia = ''
    if regs or concl:
        try:
            resumo_ia = str(ia(
                'Escreva um parágrafo curto (no máximo 5 frases), em português do Brasil, resumindo para o professor o que '
                'a equipe do laboratório fez no período. Seja factual e não invente nada além dos dados.\n'
                f"Registros do diário: {[{'data': r['data'], 'nome': r['nome'], 'atividade': r['atividade']} for r in regs]}\n"
                f"Tarefas concluídas: {[{'tarefa': t['tarefa'], 'responsaveis': t['responsaveis']} for t in concl]}")).strip()
        except Exception as e:  # noqa: BLE001 — relatório sai mesmo sem a IA
            log.warning('resumo da IA falhou: %s', e)

    doc = Document()
    st = doc.styles['Normal']
    st.font.name = 'Calibri'
    st.font.size = Pt(11)
    for nome_estilo in ('Heading 1', 'Heading 2'):
        doc.styles[nome_estilo].font.color.rgb = VERDE

    t = doc.add_heading('Relatório do laboratório', 0)
    t.runs[0].font.color.rgb = VERDE
    p = doc.add_paragraph()
    r = p.add_run(f'Reutilizar ECOnstruir · UFRJ Macaé\nPeríodo: {titulo} · gerado pelo ECOrdena em '
                  f"{datas.agora().strftime('%d/%m/%Y %H:%M')}")
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(0x6B, 0x6B, 0x6B)

    doc.add_heading('Resumo', 1)
    for linha in (f'Tarefas concluídas: {len(concl)} (previstas no período: {len(previstas)})',
                  f'Tarefas atrasadas em aberto: {len(atrasadas)}',
                  f'Registros no diário: {len(regs)} · fotos: {n_fotos}'):
        doc.add_paragraph(linha, style='List Bullet')
    if resumo_ia:
        doc.add_paragraph().add_run(resumo_ia).italic = True

    doc.add_heading('Atividades por aluno', 1)
    funcoes = {norm(m['nome']): m['funcao'] for m in store.membros() if m['tipo'] == 'aluno'}
    nomes = []
    for reg in regs:
        if reg['nome'] not in nomes:
            nomes.append(reg['nome'])
    for tarefa in concl:
        for n in [x.strip() for x in tarefa['responsaveis'].split(',') if x.strip()]:
            if n not in nomes:
                nomes.append(n)
    if not nomes:
        doc.add_paragraph('Nenhuma atividade registrada no período.')
    fotos_no_doc = 0
    for nome in nomes:
        doc.add_heading(nome + (f" — {funcoes[norm(nome)]}" if funcoes.get(norm(nome)) else ''), 2)
        for tarefa in concl:
            if norm(nome) in [norm(x) for x in tarefa['responsaveis'].split(',')]:
                doc.add_paragraph(f"✅ {tarefa['tarefa']} [{tarefa['id']}]", style='List Bullet')
        for reg in [x for x in regs if norm(x['nome']) == norm(nome)]:
            doc.add_paragraph(f"📝 {datas.data_curta(reg['data'])} — {reg['atividade']}", style='List Bullet')
            for fid in reg['fotos']:
                if fotos_no_doc >= MAX_FOTOS:
                    break
                try:
                    doc.add_picture(_foto(fid), width=Cm(8))
                    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    fotos_no_doc += 1
                except Exception as e:  # noqa: BLE001 — foto que não baixa não derruba o relatório
                    log.warning('foto %s falhou: %s', fid, e)

    doc.add_heading('Tarefas atrasadas em aberto', 1)
    if not atrasadas:
        doc.add_paragraph('Nenhuma 🎉')
    for tarefa in atrasadas:
        doc.add_paragraph(f"{datas.data_curta(tarefa['data'])} · {tarefa['tarefa']} "
                          f"({tarefa['responsaveis'] or 'sem responsável'}) [{tarefa['id']}]", style='List Bullet')

    buf = io.BytesIO()
    doc.save(buf)
    resumo = (f'✅ Tarefas concluídas: {len(concl)} (previstas: {len(previstas)})\n'
              f'⚠️ Atrasadas em aberto: {len(atrasadas)}\n'
              f'📝 Registros no diário: {len(regs)} (📷 {n_fotos} fotos)') + (f'\n\n{resumo_ia}' if resumo_ia else '')
    nome_arq = f"Relatorio_lab_{ini.replace('-', '')}_{h.replace('-', '')}.docx"
    return titulo, resumo[:3000], buf.getvalue(), nome_arq
