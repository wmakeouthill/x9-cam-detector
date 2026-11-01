@echo off
title X9 Cam Detector (Docker)
cd /d "%~dp0"
echo ========================================================
echo  Iniciando X9 Cam Detector via Docker...
echo ========================================================
docker compose up -d
echo.
echo ========================================================
echo  Sistema ativo em segundo plano!
echo  Painel Web: http://localhost:39080
echo  Rede Local: http://[seu-ip-local]:39080
echo ========================================================
pause
