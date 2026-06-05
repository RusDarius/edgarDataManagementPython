@echo off
setlocal

echo Installing Evidence SQLTools DuckDB driver VSIX into Cursor...
set VSIX=%TEMP%\sqltools-duckdb-driver.vsix
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://marketplace.visualstudio.com/_apis/public/gallery/publishers/Evidence/vsextensions/sqltools-duckdb-driver/latest/vspackage' -OutFile '%VSIX%'"
cursor --install-extension "%VSIX%"
if errorlevel 1 exit /b 1

for /f "delims=" %%D in ('powershell -NoProfile -Command "(Get-ChildItem -Path $env:USERPROFILE\.cursor\extensions -Directory | Where-Object { $_.Name -like 'evidence.sqltools-duckdb-driver-*' } | Sort-Object Name -Descending | Select-Object -First 1).FullName"') do set DRIVER_DIR=%%D

if not defined DRIVER_DIR (
  echo Could not find evidence.sqltools-duckdb-driver extension folder.
  exit /b 1
)

echo Installing compatible duckdb-async in %DRIVER_DIR% ...
pushd "%DRIVER_DIR%"
call npm.cmd install duckdb-async@1.4.2 --omit=dev --no-save
if errorlevel 1 exit /b 1
popd

set SQLTOOLS_DATA=%LOCALAPPDATA%\vscode-sqltools\Data
if not exist "%SQLTOOLS_DATA%" mkdir "%SQLTOOLS_DATA%"
pushd "%SQLTOOLS_DATA%"
if not exist package.json (
  echo {^"dependencies^": {^"duckdb-async^": ^"^1.4.2^" }}> package.json
)
call npm.cmd install duckdb-async@1.4.2
if errorlevel 1 exit /b 1
popd

echo.
echo Setup complete. Reload Cursor, then connect to a DuckDB entry in .vscode/settings.json.
echo Use databaseFilePath (not database) for Evidence driver connections.
echo If npm fails in PowerShell, run this .cmd file instead of npm.ps1 directly.

endlocal
