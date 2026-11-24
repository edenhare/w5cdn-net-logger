@echo off
setlocal
cd /d "%~dp0"
if not exist "node_modules" (
  echo Installing W5CDN Net Logger dependencies...
  call npm install
)
call npm start
