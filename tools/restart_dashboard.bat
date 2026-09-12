@echo off
@REM Точечный рестарт ТОЛЬКО dashboard-службы Hermes (порт 9119).
@REM Чат идёт через отдельный процесс hermes_cli.main serve — его не трогаем.
@REM Причина: новый REST-маршрут плагина (/skills) виден только после
@REM перезапуска процесса dashboard — роутеры монтируются на старте.
chcp 65001 >nul
set "S=HermesGateway (D:_NEURO_Hermes)"
echo -- stop:
sc stop "%S%"
timeout /t 4 /nobreak >nul
echo -- start:
sc start "%S%"
timeout /t 8 /nobreak >nul
echo -- state:
sc query "%S%" | findstr /i "STATE"
