@echo off
title Parando X9 Cam Detector
cd /d "%~dp0"
echo ========================================================
echo  Parando containers do X9 Cam Detector...
echo ========================================================
docker compose down
echo.
echo Containers parados com sucesso.
pause
