# Painel de Relatórios — Banco Pan (eLaw)

Aplicativo desktop (Tkinter) que automatiza a exportação dos relatórios do
eLaw ("DADOS DO PROCESSO", "ESCRITÓRIO - TAREFAS" e "PAUTA GERAL"), envia os
arquivos para um bucket S3 compartilhado e registra o histórico de execuções
num MongoDB remoto — para que qualquer máquina veja o mesmo status e os
mesmos relatórios, independente de quem rodou a automação.

## Arquitetura

- **`gui.py`** — interface Tkinter (lista de execuções, log ao vivo, botões
  de download). Ponto de entrada da aplicação.
- **`main.py` / `pesquisar.py`** — orquestração do pipeline de automação
  (login, filtros, exportação, download).
- **`modules/`**
  - `browser.py`, `login.py`, `elaw.py`, `download.py`, `cancel.py`,
    `progress.py` — automação Playwright do eLaw.
  - `storage.py` — upload/download dos relatórios no S3.
  - `history.py` — histórico de execuções no MongoDB (status, downloads).
  - `paths.py` — resolução de caminhos (dev vs. `.exe` empacotado).
- **`packaging/`** — script do PyInstaller (`.spec`) e do Inno Setup
  (`.iss`) para gerar o instalador Windows.

## Configuração

Credenciais e conexões ficam num `.env` na raiz do projeto (nunca versionado
— veja `.gitignore`):

```
EMAIL=...
PASS=...

AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=...

# Opcional: endpoint do manifesto de atualização para homologação.
# Em produção, o padrão é o stable.json publicado diretamente no bucket S3.
UPDATE_MANIFEST_URL=https://.../stable.json

MONGO_URI=mongodb://usuario:senha@host:porta/relatorios?authSource=admin
```

As credenciais do eLaw também podem ser editadas em runtime pela própria
GUI (botão "Credenciais").

## Rodando em desenvolvimento

```bash
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
python gui.py
```

## Build (instalador Windows)

```powershell
./build.ps1
```

Gera `dist/RelatoriosPan/` (via PyInstaller) usando `VERSION` como fonte
única de versão. O instalador final (`Setup.exe`) é gerado a partir de
`packaging/relatorios.iss` (Inno Setup), que depende da pasta `dist/` já
existir.

## Atualizações automáticas

O aplicativo consulta o manifesto `stable.json` diretamente no S3 ao iniciar.
Sem acesso à internet, ele mostra o aviso e bloqueia novas execuções. Quando
há uma versão mais recente, o painel permite baixá-la, valida o SHA-256 e
inicia silenciosamente o instalador após encerrar o aplicativo.

Para publicar uma release após validar o build:

```powershell
./build.ps1 -Version 2.0.2 -Publish
```

Esse comando envia primeiro o instalador e só então publica o manifesto, para
que os usuários nunca recebam uma referência a um arquivo inexistente. O
prefixo `operacional/orquestra/banco-pan/relatorios/releases/` no S3 precisa
ter leitura disponível aos computadores que usam o aplicativo.
