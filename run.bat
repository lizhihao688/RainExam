@echo off
chcp 65001 >nul
title RainExam - 一键提取雨课堂在线考题

setlocal enabledelayedexpansion

cls
echo ============================================
echo        🌧️  RainExam — 一键设置 + 运行
echo        雨课堂在线考题提取 ^& AI 解答工具
echo ============================================
echo.

rem ===== 1. 检查 Python =====
python --version >nul 2>&1
if !errorlevel! neq 0 (
    echo [1/4] ⚠️  未检测到 Python，正在尝试安装...
    echo.
    echo   正在打开 Python 下载页面，请按以下步骤操作：
    echo     ① 下载最新版 Python
    echo     ② 安装时务必勾选「Add Python to PATH」
    echo     ③ 安装完成后关掉本窗口，重新双击 run.bat
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>nul') do echo [1/4] ✅ %%i

rem ===== 2. 安装依赖 =====
echo [2/4] 🔍 检查依赖...
python -c "import openai, httpx" >nul 2>&1
if !errorlevel! neq 0 (
    echo   正在安装 openai、httpx...
    pip install openai httpx -q
    if !errorlevel! neq 0 (
        echo ❌ 安装失败，请检查网络后重试
        pause
        exit /b 1
    )
    echo   ✅ 依赖安装完成
) else (
    echo   ✅ 依赖已就绪
)

rem ===== 3. 检查/创建 .env =====
echo [3/4] 🔧 检查配置文件...
if not exist ".env" (
    copy .env.example .env >nul
    echo   ⚠️  已自动创建 .env 文件
    echo.
    echo   ╔══════════════════════════════════════════════╗
    echo   ║  重要！请用记事本打开 .env 文件              ║
    echo   ║  填入你的雨课堂在线 Cookie 后才能使用          ║
    echo   ║                                              ║
    echo   ║  如何在浏览器获取 Cookie：                    ║
    echo   ║  ① 登录雨课堂在线，进入考试页面               ║
    echo   ║  ② 按 F12 → Network → 刷新页面             ║
    echo   ║  ③ 点击任意请求 → 复制 Cookie 值            ║
    echo   ║  ④ 粘贴到 .env 的 XT_COOKIE= 后面          ║
    echo   ╚══════════════════════════════════════════════╝
    echo.
    pause
    start notepad .env
    echo.
    echo   编辑完 .env 后，按任意键继续...
    pause >nul
) else (
    echo   ✅ .env 配置文件就绪
)

rem ===== 4. 运行 =====
echo [4/4] 🚀 准备运行...
echo.

:input_id
set /p EXAM_ID=请输入试卷 ID（例如 4361438，输入 q 退出）：
if "%EXAM_ID%"=="" goto input_id
if /i "%EXAM_ID%"=="q" exit /b 0

echo.
echo 请选择模式：
echo   1. 仅提取题目
echo   2. 提取 + AI 解答
choice /c 12 /n /m "请按 1 或 2（默认 1）："
if !errorlevel! equ 2 (
    set MODE_FLAG=--answer
    echo   📝 模式：提取 + AI 解答
) else (
    set MODE_FLAG=
    echo   📝 模式：仅提取题目
)

cls
echo ============================================
echo   正在运行...
echo   试卷 ID: %EXAM_ID%
echo ============================================
echo.

python extract_questions.py --exam-id %EXAM_ID% !MODE_FLAG!

echo.
if !errorlevel! equ 0 (
    echo ✅ 运行完成！请查看生成的 questions.txt 文件
) else (
    echo ❌ 运行出错，请检查上面的错误信息
)
echo.
pause

goto input_id