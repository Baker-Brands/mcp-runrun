# Como instalar o mcp-runrun

## Pré-requisitos

1. **Python 3.11+** instalado ([python.org/downloads](https://www.python.org/downloads/))
2. **Claude** (Claude Code ou app Claude Desktop)
3. Suas credenciais do Runrun.it — pegue em **Runrun.it → Configurações → Integrações → API**:
   - **App-Key** — chave da organização
   - **User-Token** — token pessoal do usuário

## Instalação (deixe o Claude fazer)

Cole este prompt no Claude, preenchendo as credenciais:

> Clone o repositório **https://github.com/Baker-Brands/mcp-runrun** para uma pasta local, crie um
> ambiente virtual Python (.venv), instale o pacote com `pip install -e .`
> e registre o servidor MCP no meu Claude (Claude Code e, se eu usar o app
> Claude Desktop, também no claude_desktop_config.json). As credenciais devem
> ficar num arquivo `.env` na pasta do projeto (use o `.env.example` como
> modelo), nunca no config do Claude. Minha App-Key é **XXXX** e meu
> User-Token é **YYYY**. Ao final, teste chamando `get_current_user` e me
> confirme o nome do usuário autenticado.

Depois reinicie o Claude (⌘Q no Mac / fechar completamente no Windows) e pronto.

## Atualizar para uma nova versão

Peça ao Claude:

> Rode `git pull` na pasta do mcp-runrun e reinstale com `pip install -e .`

## O que o servidor faz

Veja o [README.md](README.md) — tarefas, projetos, clientes, quadros, times,
comentários, apontamento de horas, exportação de cards para planilha e
leitura/escrita de campos personalizados.
