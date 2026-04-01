@echo off
REM ============================================================
REM  setup_cron_windows.bat
REM  Configure le Planificateur de tâches Windows pour lancer
REM  daily_run.py chaque matin à 6h00.
REM
REM  USAGE : Double-cliquer ou lancer en administrateur
REM          (clic droit → "Exécuter en tant qu'administrateur")
REM ============================================================

echo.
echo  === CONFIGURATION CRON WINDOWS — Pipeline Innovorder ===
echo.

REM Chemin vers ce dossier
SET SCRIPT_DIR=%~dp0
SET SCRIPT=%SCRIPT_DIR%daily_run.py
SET LOG=%SCRIPT_DIR%logs\scheduler.log
SET TASK_NAME=InnovorderDailyPipeline

REM Détecte Python automatiquement depuis le PATH
FOR /F "tokens=*" %%i IN ('where python 2^>nul') DO (
    IF "%%i" NEQ "" SET PYTHON=%%i
    GOTO :python_found
)
:python_found

IF "%PYTHON%"=="" (
    echo  [ERREUR] Python introuvable dans le PATH.
    echo  Installe Python depuis python.org ou verifie ton PATH.
    pause
    exit /b 1
)

echo  Python   : %PYTHON%
echo  Script   : %SCRIPT%
echo  Tâche    : %TASK_NAME%
echo  Horaire  : Chaque jour à 06:00
echo.

REM Supprime la tâche si elle existe déjà (pour éviter les doublons)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Crée la tâche planifiée
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\" --days 1 --limit 15" ^
  /sc DAILY ^
  /st 06:00 ^
  /ru "%USERNAME%" ^
  /rl HIGHEST ^
  /f

IF %ERRORLEVEL% EQU 0 (
    echo.
    echo  [OK] Tâche planifiée créée avec succès !
    echo.
    echo  Le pipeline BODACC tournera chaque matin à 06:00.
    echo  Les logs seront dans : %SCRIPT_DIR%logs\
    echo.
    echo  Pour vérifier : ouvrir "Planificateur de tâches" Windows
    echo  Ou lancer maintenant : schtasks /run /tn "%TASK_NAME%"
) ELSE (
    echo.
    echo  [ERREUR] Echec de création. Lance ce fichier en administrateur.
)

echo.
pause
