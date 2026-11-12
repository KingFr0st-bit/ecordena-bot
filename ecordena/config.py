"""Configuração lida das variáveis de ambiente (no Render: Environment)."""
import os

TZ = 'America/Sao_Paulo'
DIAS_MOLHAGEM = 7          # dias seguidos de molhagem após a moldagem
PESSOAS_POR_MOLHAGEM = 2   # mínimo de pessoas por molhagem


def _env(nome, padrao=''):
    return (os.environ.get(nome) or padrao).strip()


class Config:
    @property
    def telegram(self):          # token do BotFather (espaços colados sem querer são ignorados)
        return _env('TELEGRAM_TOKEN').replace(' ', '')

    @property
    def groq(self):
        return _env('GROQ_KEY').replace(' ', '')

    @property
    def gemini(self):
        return _env('GEMINI_KEY').replace(' ', '')

    @property
    def planilha(self):          # ID da planilha (o trecho entre /d/ e /edit na URL)
        return _env('SHEET_ID')

    @property
    def credenciais(self):       # conteúdo do JSON da conta de serviço do Google
        return _env('GOOGLE_CREDENTIALS')

    @property
    def segredo(self):           # protege o webhook, o /cron e o /setup
        return _env('APP_SECRET')

    @property
    def url_publica(self):       # o Render preenche RENDER_EXTERNAL_URL sozinho
        return _env('PUBLIC_URL') or _env('RENDER_EXTERNAL_URL')

    @property
    def modelo_groq(self):
        return _env('GROQ_MODEL')

    @property
    def modelo_gemini(self):
        return _env('GEMINI_MODEL', 'gemini-2.5-flash')


cfg = Config()
