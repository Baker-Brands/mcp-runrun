@echo off
rem Wrapper do mcp-runrun (Windows): carrega credenciais do .env e inicia o servidor MCP.
setlocal
cd /d "%~dp0"
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
)
"%~dp0.venv\Scripts\mcp-runrun.exe"
