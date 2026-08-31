@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"
if errorlevel 1 (
    echo [MediPet] 无法进入项目目录：%~dp0
    exit /b 1
)

where.exe docker >nul 2>&1
if errorlevel 1 (
    echo [MediPet] 未找到 Docker。请安装并启动 Docker Desktop 后重试。
    exit /b 1
)

call docker compose version >nul 2>&1
if errorlevel 1 (
    echo [MediPet] Docker Compose 插件不可用。请更新 Docker Desktop 后重试。
    exit /b 1
)

call docker info >nul 2>&1
if errorlevel 1 (
    echo [MediPet] Docker Engine 未运行。请启动 Docker Desktop，等待其就绪后重试。
    exit /b 1
)

call docker compose up --build
exit /b %ERRORLEVEL%
