@echo off
setlocal

set APP_NAME=caesar-desktop
set APP_TITLE=CAESAR Desktop

set /p VERSION=<version.txt

set OUTPUT_NAME=CAESAR_Desktop_v%VERSION%_windows_x86_64.exe

echo =========================================
echo  %APP_TITLE%
echo  Version: %VERSION%
echo =========================================

echo =========================================
echo  Cleaning old build files
echo =========================================

if exist build     rmdir /s /q build
if exist dist      rmdir /s /q dist
if exist AppDir    rmdir /s /q AppDir
if exist *.spec    del /q *.spec

echo =========================================
echo  Generating version.py
echo =========================================

echo VERSION = "%VERSION%" > version.py

echo =========================================
echo  Building executable with PyInstaller
echo =========================================

call venv\Scripts\activate

pyinstaller ^
    --onefile ^
    --windowed ^
    --name "%APP_NAME%" ^
    main.py

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed.
    exit /b 1
)

echo =========================================
echo  Moving to Releases
echo =========================================

if not exist Releases mkdir Releases

move "dist\%APP_NAME%.exe" "Releases\%OUTPUT_NAME%"

echo.
echo =========================================
echo  BUILD COMPLETE
echo =========================================

dir "Releases\%OUTPUT_NAME%"

endlocal
