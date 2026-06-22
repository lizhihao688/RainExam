#!/bin/bash
# RainExam — 一键环境准备 + 运行（macOS / Linux）

set -e

clear
echo "============================================"
echo "       🌧️  RainExam — 一键设置 + 运行"
echo "       雨课堂在线考题提取 & AI 解答工具"
echo "============================================"
echo ""

# ===== 1. 检查 Python =====
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[1/4] ⚠️  未检测到 Python"
    echo ""
    echo "  请先安装 Python 3.10+："
    echo "    macOS:   brew install python@3.12"
    echo "    Ubuntu:  sudo apt install python3 python3-pip"
    echo "    或从 https://www.python.org/downloads/ 下载"
    echo ""
    read -p "按回车键退出..."
    exit 1
fi

PY_VERSION=$($PYTHON --version 2>&1)
echo "[1/4] ✅ $PY_VERSION"

# ===== 2. 检查/安装依赖 =====
echo "[2/4] 🔍 检查依赖..."
if $PYTHON -c "import openai, httpx" 2>/dev/null; then
    echo "      ✅ 依赖已就绪"
else
    echo "      正在安装 openai、httpx..."
    $PYTHON -m pip install openai httpx -q
    echo "      ✅ 依赖安装完成"
fi

# ===== 3. 检查/创建 .env =====
echo "[3/4] 🔧 检查配置文件..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "      ⚠️  已自动创建 .env 文件"
    echo ""
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║  重要！请编辑 .env 文件，填入你的            ║"
    echo "  ║  雨课堂在线 Cookie 后才能使用                  ║"
    echo "  ║                                              ║"
    echo "  ║  获取方式：                                   ║"
    echo "  ║  ① 登录雨课堂，进入考试页面               ║"
    echo "  ║  ② 按 F12 → Network → 刷新页面             ║"
    echo "  ║  ③ 点击任意请求 → 复制 Cookie 值            ║"
    echo "  ║  ④ 粘贴到 .env 的 XT_COOKIE= 后面          ║"
    echo "  ╚══════════════════════════════════════════════╝"
    echo ""
    read -p "编辑完成后，按回车键继续..."
else
    echo "      ✅ .env 配置文件就绪"
fi

# ===== 4. 运行 =====
echo "[4/4] 🚀 准备运行"
echo ""

while true; do
    read -p "请输入试卷 ID（例如 4361438，输入 q 退出）: " EXAM_ID
    [ "$EXAM_ID" = "q" ] && exit 0
    [ -z "$EXAM_ID" ] && echo "试卷 ID 不能为空" && continue

    echo ""
    echo "请选择模式："
    echo "  1) 仅提取题目"
    echo "  2) 提取 + AI 解答"
    read -p "请输入 1 或 2: " MODE

    MODE_FLAG=""
    [ "$MODE" = "2" ] && MODE_FLAG="--answer"

    clear
    echo "============================================"
    echo "  正在运行…"
    echo "  试卷 ID: $EXAM_ID"
    echo "============================================"
    echo ""

    $PYTHON src/extract_questions.py --exam-id "$EXAM_ID" $MODE_FLAG

    echo ""
    if [ $? -eq 0 ]; then
        echo "✅ 运行完成！请查看生成的 questions.txt 文件"
    else
        echo "❌ 运行出错，请检查上面的错误信息"
    fi
    echo ""
    read -p "按回车键继续..."
done