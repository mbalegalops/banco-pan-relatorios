# Changelog

Todas as mudanças notáveis neste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/),
e este projeto segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Added
- **Funcionalidade de Retry Individual**: Novo endpoint `POST /api/retentar/{run_id}/{nome_relatorio}` permite que usuários retentem apenas relatórios que falharam, sem precisar reiniciar a execução inteira
- **Salvamento em SharePoint**: Relatórios são salvos paralelamente em pasta local sincronizada com SharePoint via OneDrive, com retry automático (3 tentativas)
- **Botão Mostrar/Ocultar Senha**: Ícone 👁 nas credenciais permite revelar a senha para verificação (muda para 🔓 quando visível)
- **Registro de Relatórios Falhados**: MongoDB agora armazena `failed_reports` com IDs dos relatórios que falharam, permitindo retry posterior
- **Histórico de Falhados**: Novos métodos em `modules/history.py`:
  - `registrar_relatorio_falhado()`: Registra relatório que falhou com seu ID
  - `remover_relatorio_falhado()`: Remove de falhados quando retry bem-sucedido

### Changed
- **Timeout aumentado**: De 1 hora (3600s) para 3 horas (10800s)
  - Permite processamento de relatórios maiores (ex: PAUTA GERAL com ano completo)
  - Compatível com a pauta_general que já tinha timeout de 600s para exportação
  
- **Resiliência a Falhas Parciais**: Comportamento melhorado quando um ou mais relatórios falham
  - Antes: Se um relatório falhava, nenhum era disponibilizado
  - Depois: Relatórios bem-sucedidos ficam disponíveis mesmo que alguns falhem
  - Erros de exportação não interrompem outros relatórios
  - Erros de download não interrompem outros relatórios
  - Timeout não interrompe execução, apenas marca como erro
  
- **Fluxo de Execução**: Agora suporta falhas parciais em todo o pipeline
  - `pesquisar.py`: Try-except envolvem exportações individuais
  - `modules/download.py`: Try-except envolvem downloads individuais
  - `pipeline.py`: Aceita falhas parciais como sucesso com avisos
  - `web.py`: Registra execução mesmo com relatórios falhados

- **Retorno de `aguardar_e_baixar_relatorios()`**: 
  - Antes: Retornava `(baixados, page)`
  - Depois: Retorna `(baixados, falhados_com_id, page)` para permitir retry posterior

- **Módulo SharePoint**: Novo arquivo `modules/sharepoint.py`
  - Copia relatórios para pasta local sincronizada
  - Detecta usuário Windows automaticamente (`os.getenv('USERNAME')`)
  - Funciona em qualquer máquina com OneDrive ativo
  - Retry 3x com delay entre tentativas
  - Não bloqueia pipeline (assíncrono em thread)

### Fixed
- Lógica de captura de relatórios falhados em `modules/download.py`
  - Apenas relatórios com ID no elaw podem ser retentados
  - Timeout agora registra erro sem quebrar execução

### Technical Details

#### Arquivos Modificados
- `modules/download.py` (+92 linhas)
  - Aumentar `POLL_TIMEOUT` de 3600 para 10800
  - Adicionar try-except em downloads
  - Nova função `fazer_download_relatorio_individual()`
  - Retornar dict de falhados para retry
  
- `modules/sharepoint.py` (novo arquivo, +70 linhas)
  - `obter_pasta_sharepoint()`: Constrói caminho com username
  - `enviar_relatorio_sharepoint()`: Cópia assíncrona com retry
  
- `modules/history.py` (+25 linhas)
  - `registrar_relatorio_falhado()`: Salva ID de relatório que falhou
  - `remover_relatorio_falhado()`: Remove após retry bem-sucedido
  
- `pesquisar.py` (+30 linhas)
  - Try-except em exportações
  - Parâmetro `run_id` opcional
  - Registra falhados no histórico
  
- `pipeline.py` (+2 linhas)
  - Parâmetro `run_id` opcional
  - Passa para `pesquisar()`
  
- `web.py` (+70 linhas)
  - Novo endpoint `POST /api/retentar/{run_id}/{nome_relatorio}`
  - `_retry_worker()`: Thread worker para retry
  - Passa `run_id` para `executar()`
  
- `static/index.html` (+5 linhas)
  - Campo de senha envolvido em `.password-field`
  - Botão `#btnToggleSenha` com ícone 👁
  
- `static/style.css` (+20 linhas)
  - Estilos para `.password-field` e `.btn-toggle-password`
  - Botão posicionado sobre o campo
  
- `static/app.js` (+12 linhas)
  - Referência a `#btnToggleSenha`
  - Event listener para alternar `type="password"` ↔ `type="text"`

#### Estrutura de Dados MongoDB

Campo adicionado em `runs`:
```javascript
{
  "_id": 1,
  "started_at": "2026-01-15T10:30:00",
  "finished_at": "2026-01-15T10:45:00",
  "status": "success",
  "downloads": {
    "DADOS DO PROCESSO": "s3://...",
    "ESCRITÓRIO - TAREFAS": "s3://...",
  },
  "failed_reports": {  // ← NOVO
    "PAUTA GERAL": "report_id_12345"  // Permite retry
  },
  "error": null,
  "usuario": "arthur.viana",
  "log_uri": "s3://..."
}
```

#### Novos Endpoints

`POST /api/retentar/{run_id}/{nome_relatorio}`
- **Request**: Nenhum body
- **Response**: `{ "ok": true, "status": "retrying" }`
- **Errors**:
  - 409: Execução já em andamento
  - 404: Execução não encontrada
  - 404: Relatório não marcado como falhado

#### Novo Caminho SharePoint

Estrutura criada automaticamente:
```
C:\Users\{USERNAME}\Mascarenhas Barbosa Advogados\MBA - Robô de Checagem\
└── 2026-01-15/
    ├── DADOS_PROCESSO_20260115_103000.xlsx
    ├── ESCRITORIO_TAREFAS_20260115_103500.xlsx
    └── PAUTA_GERAL_20260115_104000.xlsx
```

Sincronizado automaticamente com SharePoint Online via OneDrive.

### Breaking Changes
Nenhuma breaking change. Todas as mudanças são retrocompatíveis.

### Migration Guide

**Para versões anteriores:**
1. Não é necessária migração de dados
2. MongoDB adicionará campo `failed_reports` automaticamente ao primeiro erro
3. SharePoint começa a salvar automaticamente na próxima execução

### Testing Checklist

- [ ] Timeout 3h: Executar com relatório que demora 2h30m
- [ ] Resiliência: Simular falha de um relatório, verificar se outros 2 são salvos no S3
- [ ] SharePoint: Verificar que arquivo aparece na pasta local sincronizada
- [ ] Interface: Botões de download aparecem para relatórios bem-sucedidos mesmo com falha parcial
- [ ] Retry Individual:
  - [ ] Executar e deixar um relatório falhar
  - [ ] Clicar em "Retentar" na interface
  - [ ] Verificar que só aquele relatório é retentado
  - [ ] Verificar status "retrying" → "success" ou "error"
- [ ] Histórico: Verificar que `failed_reports` é atualizado e removido após retry
- [ ] Senha Visível: Clicar em 👁 nas credenciais, verificar mudança para 🔓

### Performance Impact

- **Timeout**: Aumenta tempo máximo de espera, sem impacto em execuções normais
- **SharePoint**: +0-100ms por relatório (assíncrono, não bloqueia)
- **Retry**: Apenas quando usuário solicita, sem impacto em execuções normais
- **MongoDB**: +1 operação de escrita por relatório falhado

### Security Considerations

- ⚠️ Senha agora pode ser visível na interface (solicitação de usuário para facilitar para leigos)
  - Ainda é criptografada em repouso (no .env local)
  - Ainda é criptografada em trânsito (HTTPS se disponível)
  - Apenas o operador da máquina pode ver
  
- Retry não expõe informações de segurança adicionais
- SharePoint local acesso controla via permissões do Windows

---

## [2.0.0] - 2026-01-10

Primeira versão com interface web (FastAPI).

### Added
- Interface web em FastAPI (substituindo Tkinter)
- Logs em tempo real via WebSocket
- Histórico de execuções em MongoDB
- Upload automático de relatórios para S3
- Suporte para múltiplas máquinas (histórico centralizado)

### Changed
- Migração de Tkinter para web
- Login e automação separados em módulos

### Fixed
- Problema de sincronização em execuções simultâneas

---

## [1.0.0] - 2025-12-01

Versão inicial com GUI Tkinter.

### Added
- Interface gráfica com Tkinter
- Automação de login no eLaw
- Exportação de 3 relatórios (DADOS, TAREFAS, PAUTA)
- Download com polling
- Upload para S3

[Unreleased]: https://github.com/seu-usuario/seu-repo/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/seu-usuario/seu-repo/releases/tag/v2.0.0
[1.0.0]: https://github.com/seu-usuario/seu-repo/releases/tag/v1.0.0
