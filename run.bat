@echo off
REM Launcher pour le crawler de site web
REM Auteur: A.

cd /d "%~dp0"

echo ========================================
echo SITE CRAWLER - Lancement
echo ========================================
echo.

REM Verifie si Python est installe
python --version >nul 2>&1
if errorlevel 1 (
    echo ERREUR: Python n'est pas installe ou pas dans le PATH
    pause
    exit /b 1
)

echo Python detecte: 
python --version
echo.

REM Verifie les dependances Python
echo Verification des dependances...
pip show playwright >nul 2>&1
if errorlevel 1 (
    echo Installation des dependances Python...
    pip install requests beautifulsoup4 playwright
    if errorlevel 1 (
        echo ERREUR: Echec de l'installation des dependances
        pause
        exit /b 1
    )
)
echo Dependances Python OK
echo.

REM Verifie/installe Chromium (rapide si deja present)
echo Verification du navigateur Chromium...
playwright install chromium >nul 2>&1
if errorlevel 1 (
    echo Installation de Chromium...
    playwright install chromium
    if errorlevel 1 (
        echo ERREUR: Echec de l'installation de Chromium
        pause
        exit /b 1
    )
)
echo Chromium OK
echo.

REM Lance le script Python
echo Lancement du crawler...
echo.
python site_crawler.py

echo.
echo Appuyez sur une touche pour fermer...
pause >nul