# Como instalar o mcp-runrun

## Pré-requisitos

1. **Python 3.11+** instalado ([python.org/downloads](https://www.python.org/downloads/))
2. **Claude** (Claude Code ou app Claude Desktop)
3. Suas credenciais do Runrun.it — pegue em **Runrun.it → Configurações →
   Integrações → API**:
   - **App-Key** — chave da organização (a mesma para todos)
   - **User-Token** — token pessoal, individual de cada usuário

## Instalação (deixe o Claude fazer)

Troque os dois `COLE_AQUI` pelas suas chaves e cole o prompt inteiro no
Claude Code:

```
Instale o servidor MCP do Runrun.it para mim, do zero até funcionar:

1. Clone https://github.com/Baker-Brands/mcp-runrun para uma pasta no meu
   usuário (ex.: ~/mcp-runrun no Mac, C:\mcp-runrun no Windows).
2. Verifique se tenho Python 3.11+; se não tiver, me oriente a instalar antes
   de continuar. Crie um ambiente virtual .venv dentro da pasta e instale o
   pacote com pip install -e .
3. Crie o arquivo .env na raiz da pasta (modelo em .env.example) com minhas
   credenciais — elas devem ficar SÓ nesse arquivo, nunca no config do Claude:
   RUNRUN_APP_KEY=COLE_AQUI
   RUNRUN_USER_TOKEN=COLE_AQUI
4. Registre o servidor no Claude Code com escopo de usuário, usando o wrapper
   da minha plataforma (run.sh no Mac/Linux, run.bat no Windows):
   claude mcp add --scope user runrun -- <caminho-completo-do-wrapper>
   Se eu também usar o app Claude Desktop, adicione o mesmo comando ao
   claude_desktop_config.json.
5. Teste a instalação chamando a ferramenta get_current_user e me confirme
   o nome do usuário autenticado. Se der erro de autenticação, me peça para
   conferir as chaves.
6. Ao final, me lembre de reiniciar o Claude para as ferramentas aparecerem.
```

Prefere não colar o token no chat? Envie o prompt com os `COLE_AQUI`
intactos — quando o teste falhar, o Claude vai pedir para você preencher o
`.env`, e você edita o arquivo você mesmo.

Depois reinicie o Claude (⌘Q no Mac / fechar completamente no Windows) e
pronto: peça "liste minhas tarefas no Runrun" para conferir.

## Atualizar para uma nova versão

Peça ao Claude:

> Rode `git pull` na pasta do mcp-runrun e reinstale com `pip install -e .`

## Segurança

- O **User-Token equivale à sua senha** do Runrun.it — não compartilhe.
- As chaves ficam apenas no arquivo `.env`, que nunca vai para o git.
- Se um token vazar, regenere em Runrun.it → Configurações → Integrações → API.

## O que o servidor faz

Veja o [README.md](README.md) — tarefas, projetos, clientes, quadros, times,
comentários, apontamento de horas, exportação de cards para planilha e
leitura/escrita de campos personalizados.
