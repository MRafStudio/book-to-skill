@echo off
@REM Точечный рестарт ТОЛЬКО dashboard-службы Hermes (роутеры плагина монтируются на старте).
@REM Логика и поиск службы — в restart_dashboard.py: имя службы зависит от пути
@REM установки Hermes («HermesGateway (D:_NEURO_Hermes)»), поэтому в .bat его
@REM держать нельзя — иначе на чужой машине рестарт молча не найдёт службу.
chcp 65001 >nul
set "PY="
if defined B2S_PYTHON set "PY=%B2S_PYTHON%"
if not defined PY (
  for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY set "PY=%%p"
)
if not defined PY (
  echo Не нашёл python (ни B2S_PYTHON, ни в PATH^) — установи зависимости форка: hermes/INSTALL.md
  pause
  exit /b 1
)
"%PY%" "%~dp0restart_dashboard.py" %*
