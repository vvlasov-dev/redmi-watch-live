@echo off
REM Open Claude Code IN THIS PROJECT so the agent workflow auto-loads:
REM   CLAUDE.md, docs/CONVENTIONS.md, /plan, /review, .cursor/rules, the reviewer agent.
REM Launching from anywhere else loads only the GLOBAL ~/.claude config, not this repo's.
cd /d "%~dp0"
echo Redmi Watch 5 Live — agent dev session (cwd = project root)
echo   commands: /plan  /review    conventions: docs\CONVENTIONS.md
claude %*
