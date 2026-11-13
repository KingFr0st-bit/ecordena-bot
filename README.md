# 🧱 ECOrdena Bot

**Assistente com IA no Telegram que organiza tarefas, horários e o diário do laboratório** do projeto de extensão
[Reutilizar ECOnstruir](#contexto) (UFRJ Macaé), que produz tijolos de solo-cimento com EPS reciclado.

> Antes, toda a logística do lab (quem molha os tijolos, quem prepara o solo, quem está disponível) ficava na cabeça
> do professor e em mensagens de WhatsApp. O ECOrdena tira esse peso: os alunos falam com o bot em linguagem natural
> e o professor só aprova e delega.

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram_Bot_API-26A5E4?logo=telegram&logoColor=white)
![Google Sheets](https://img.shields.io/badge/Google_Sheets-34A853?logo=googlesheets&logoColor=white)
![Groq](https://img.shields.io/badge/IA-Groq_%2B_Gemini-F55036)
![Render](https://img.shields.io/badge/Deploy-Render-46E3B7?logo=render&logoColor=white)

---

## ✨ O que ele faz

| Para quem | Funcionalidade |
|---|---|
| 🎓 **Alunos** | Cadastro pelo próprio Telegram, `/hoje` e `/semana` com as tarefas, atualizar horários por texto ou **áudio** ("tô livre terça e quinta à tarde"), avisar que terminou uma tarefa |
| 📝 **Diário do lab** | O aluno conta o que fez ("hoje moldei 12 tijolos do lote 6") e manda **fotos**; tudo fica registrado com data |
| 👨‍🏫 **Professor** | Delega falando normal: *"Davi faz a molhagem amanhã"* (direto) ou *"preciso que alguém destorroe o solo"* (a IA escolhe quem combina mais pela função, disponibilidade e carga, pergunta se a pessoa sabe fazer e, se não souber, passa pro próximo) |
| 🧾 **Relatórios** | `/relatorio` gera um **DOCX com fotos** do dia, semana ou mês, com resumo escrito pela IA |
| 🗓️ **Escala com IA** | Todo domingo a IA monta a escala da semana respeitando as regras do lab (ex.: molhagem diária nos 7 primeiros dias de cura, mínimo de 2 pessoas). O professor aprova com um toque |
| 📅 **Agenda pessoal** | Botão "Adicionar ao Google Agenda" em cada tarefa e um link `.ics` que o aluno assina uma vez no celular |
| ⏰ **Automático** | Lembretes às 7h, resumo semanal na sexta e avisos no Telegram quando o professor muda algo na planilha |

## 🧠 Decisões de projeto

- **A IA propõe, o código confere.** Toda resposta da IA (nomes, datas, número de pessoas) é validada antes de ir
  pra planilha; nomes fora da equipe são descartados e itens suspeitos ganham um alerta para o professor revisar.
- **Humano no controle.** Professores só são aprovados direto na planilha (segurança); nenhuma escala vale sem aprovação.
- **IA com reserva.** Groq como principal e Gemini de reserva, com troca automática de modelo quando um é aposentado ou
  fica sobrecarregado. Se as duas falharem, os comandos continuam funcionando porque leem direto da planilha.
- **Custo zero.** Render (plano grátis) + Google Sheets como banco + fotos guardadas pelo próprio Telegram
  (o disco do Render grátis é apagado a cada reinício).

## 🏗️ Arquitetura

```
Telegram ──webhook──▶ FastAPI (Render) ──▶ lógica do bot ──▶ Google Sheets (gspread)
                          │                     │
cron-job.org ──/cron──────┘                     ├──▶ IA: Groq (texto + Whisper p/ áudio) / Gemini
(a cada 10 min: lembretes,                      └──▶ python-docx + Pillow (relatórios com fotos)
 resumo, escala, avisos)
```

| Arquivo | Papel |
|---|---|
| `ecordena/app.py` | Rotas: webhook do Telegram, `/cron`, `/setup`, agenda `.ics` |
| `ecordena/logica.py` | Cadastro, conversa com a IA, delegação, diário, escala, lembretes |
| `ecordena/store.py` | Acesso à planilha (com cache por mensagem) e o "Painel do Professor" |
| `ecordena/ia.py` | Camada de IA com fallback Groq → Gemini |
| `ecordena/relatorio.py` | Relatório DOCX com fotos |
| `ecordena/agenda.py` | Links do Google Agenda e feed iCalendar |
| `tests/simulacao.py` | Simulação ponta a ponta sem internet (Telegram, IA e planilha falsos) |

## 🚀 Rodando

1. Crie um bot no **@BotFather**, uma planilha no Google Sheets e uma **conta de serviço** no Google Cloud
   (Google Sheets API ativada; compartilhe a planilha com o e-mail dela como Editor).
2. Faça deploy no **Render** com o `render.yaml` (New → Blueprint) e preencha as variáveis:
   `TELEGRAM_TOKEN`, `GROQ_KEY`, `GEMINI_KEY` (opcional), `SHEET_ID`, `GOOGLE_CREDENTIALS` (JSON da conta de serviço)
   e `APP_SECRET` (só letras e números).
3. Abra `https://SEU-SERVICO.onrender.com/setup?key=APP_SECRET`: cria as abas da planilha e conecta o webhook.
4. Agende `https://SEU-SERVICO.onrender.com/cron?key=APP_SECRET` a cada 10 minutos no **cron-job.org**.

Teste local, sem internet:

```bash
pip install -r requirements.txt
python tests/simulacao.py
```

## 📌 Contexto

Projeto desenvolvido por **Davi Sales** (Engenharia Civil, UFRJ Macaé) para a disciplina que pedia uma solução com IA
para um problema real (Modalidade 3), aplicado ao laboratório do projeto de extensão **Reutilizar ECOnstruir**.
A primeira versão foi feita em Google Apps Script; esta é a versão 2, reescrita em Python para rodar de forma independente.

## ⚠️ Limitações conhecidas

- Plano grátis do Render: sem o despertador, o serviço dorme após 15 min e a primeira resposta demora ~1 min.
- Avisos de mudanças feitas direto na planilha chegam em até 10 min (não é instantâneo).
- Agendas assinadas (`.ics`) atualizam a cada poucas horas, por limitação do Google Agenda.
- Planos grátis de IA têm limite de uso por minuto/dia.
