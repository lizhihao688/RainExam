@echo off
chcp 65001 >nul
title RainExam - 雨课堂在线考题提取工具

echo ============================================
echo   RainExam - 雨课堂在线考题提取工具
echo ============================================
echo.

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python！请先安装 Python 3.10+
    echo.
    echo 下载地址: https://www.python.org/downloads/
    echo 安装时记得勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

python --version

:: 检查 .env
if not exist ".env" (
    echo.
    echo [提示] 未找到 .env 配置文件
    echo   - 请复制 .env.example 为 .env
    echo   - 或用记事本编辑 .env 填入 Cookie
    echo.
    pause
    exit /b 1
)

:: 检查依赖
python -c "import openai, httpx" >nul 2>&1
if %errorlevel% neq 0 (
    echo [提示] 正在安装依赖（openai, httpx）...
    pip install openai httpx
    if %errorlevel% neq 0 (
        echo [错误] 依赖安装失败！
        pause
        exit /b 1
    )
    echo 依赖安装完成！
    echo.
)

:: 主菜单
:menu
cls
echo ============================================
echo   RainExam - 选择题号模式
echo ============================================
echo.
set /p EXAM_ID=请输入试卷 ID（例如 4361438）：
if "%EXAM_ID%"=="" goto menu

echo.
echo 请选择模式：
echo   1. 仅提取题目
echo   2. 提取 + AI 解答
echo.
set /p MODE=请输入 1 或 2（回车默认 1）：
if "%MODE%"=="" set MODE=1

cls
echo ============================================
echo   正在运行...
echo   试卷 ID: %EXAM_ID%
echo ============================================
echo.

if "%MODE%"=="2" (
    python extract_questions.py --exam-id %EXAM_ID% --answer
) else (
    python extract_questions.py --exam-id %EXAM_ID%
)

echo.
if %errorlevel% equ 0 (
    echo ✅ 运行完成！请查看生成的 questions_*.txt 文件
) else (
    echo ❌ 运行出错，请检查上面的错误信息
)

echo.
pause