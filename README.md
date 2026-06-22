# RainExam 

**雨课堂在线考题提取 & AI 自动解答工具**

一键拉取学堂在线试卷，提取为格式化文本，并支持通过 AI 自动解答。

---

## ✨ 功能

| 功能 | 说明 |
|------|------|
| 📥 **在线拉取** | 从学堂在线 API 直接拉取试卷 JSON |
| 🗂️ **本地文件** | 同样支持已有的 JSON 文件 |
| 🔄 **题型支持** | 选择题 (A/B/C/D)、判断题 (T/F)、填空题 |
| 🤖 **AI 解答** | 调用任意 OpenAI 兼容 API 自动答题 + 解析 |
| 📋 **答案速查** | 每题答案内联标注 + 文件末尾速查表 |
| 📄 **分组输出** | 每 N 题一个 TXT 文件（默认 50 题/组） |
| ⚙️ **配置省心** | 通过 `.env` 文件管理配置，无需每次敲参数 |

---

## 🚀 快速开始

### 安装

```bash
git clone <repo-url>
cd rainexam

# 方式一：使用 uv（推荐）
uv sync

# 方式二：使用 pip
pip install openai httpx
```

### 配置

```bash
cp .env.example .env
```

编辑 `.env`，根据需要填写配置项（详见下文）。

### 使用

```bash
# 从本地 JSON 文件提取题目
python extract_questions.py /path/to/exam.json

# 从学堂在线 API 拉取并提取
python extract_questions.py --fetch --exam-id 4361438

# 提取 + AI 解答
python extract_questions.py exam.json --answer

# 终极懒人模式：配置好 .env 后直接运行
python extract_questions.py
```

---

## 📖 详细用法

### 数据来源

**方式 1：本地 JSON 文件**

```bash
python extract_questions.py data.json
```

或通过 `.env` 配置默认路径：

```ini
JSON_PATH=/path/to/exam.json
```

之后可直接运行 `python extract_questions.py`。

**方式 2：从学堂在线 API 拉取**

```bash
# 先设置 Cookie（从浏览器开发者工具复制）
export XT_COOKIE='xt_lang=zh; x_access_token=eyJ...'

# 拉取试卷 ID 为 4361438 的考题
python extract_questions.py --fetch --exam-id 4361438
```

Cookie 获取方式：打开学堂在线考试页面 → 按 F12 打开开发者工具 → Network 标签 → 刷新 → 点击任意 API 请求 → 复制 Request Headers 中的 Cookie 字符串。

### AI 解答

```bash
# 使用 OpenAI
python extract_questions.py exam.json --answer \
  --ai-api-key sk-xxx \
  --ai-model gpt-4o-mini

# 使用 DeepSeek
AI_BASE_URL=https://api.deepseek.com/v1 \
AI_API_KEY=sk-xxx \
python extract_questions.py exam.json --answer

# 使用通义千问（阿里云）
AI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
AI_API_KEY=sk-xxx \
python extract_questions.py exam.json --answer
```

AI 解答会自动识别题型：

| 题型 | 识别方式 | AI 回答格式 |
|------|---------|------------|
| 选择题 | `Type: ""` 或有多选项 | 选 A/B/C/D + 解析 |
| 判断题 | `Type: "Judgement"` | 选 T/F + 解析 |
| 填空题 | `Type: "FillBlank"` | 填写文本答案 + 解析 |

### 完整搭配

```bash
# 拉取 → 提取 → 解答 → 输出，一条命令搞定
XT_COOKIE='xt_lang=zh; x_access_token=xxx' \
AI_API_KEY=sk-xxx \
AI_MODEL=deepseek-chat \
python extract_questions.py --fetch --exam-id 4361438 --answer
```

---

## ⚙️ 配置参考

所有配置项可通过三种方式设置（优先级：命令行 > 环境变量 > `.env` 文件）：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `JSON_PATH` | 本地 JSON 文件路径 | — |
| `XT_COOKIE` | 学堂在线 Cookie | — |
| `AI_API_KEY` | AI API Key | — |
| `OPENAI_API_KEY` | AI API Key（别名） | — |
| `AI_BASE_URL` | AI API 地址 | `https://api.openai.com/v1` |
| `AI_MODEL` | AI 模型名称 | `gpt-4o-mini` |
| `PAGE_SIZE` | 每个文件包含的题数 | `50` |

### `.env` 示例

```ini
JSON_PATH=/Users/me/exam.json
XT_COOKIE=xt_lang=zh; x_access_token=eyJ0eXAiOi...
AI_API_KEY=sk-your-api-key
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
```

---

## 📂 输出示例

### questions_1.txt

```
题目 1
A ____ is a list of instructions for the computer to follow...
A. systems design
B. word processor
C. program      ← ✅
D. systems analysis

📌 答案：C
💡 解析：A program is a set of instructions that directs a computer...

题目 2
Coding is only one of the six steps of programming. (  )
T. 正确
F. 错误      ← ✅

📌 答案：F
💡 解析：There are six steps: ... coding is just one of them...

═══════════════════════════════════════════════════════════
📋 答案速查（第 1-50 题）
═══════════════════════════════════════════════════════════
   1: C       2: F       3: D       4: C       5: A
   6: D       7: D       8: A       9: A      10: A
...
═══════════════════════════════════════════════════════════
```

---

## 🧩 支持的数据格式

项目从 JSON 中提取 `data.problems` 数组，每题支持以下字段：

```json
{
  "Body": "<p>题目内容</p>",
  "Type": "Judgement / FillBlank",
  "Options": [
    {"key": "true", "value": "正确"},
    {"key": "false", "value": "错误"}
  ],
  "ProblemType": 6
}
```

题型自动识别：
- `Type` 未指定 + 有选项 → 选择题
- `Type: "Judgement"` 或 `ProblemType: 6` → 判断题
- `Type: "FillBlank"` 或 `ProblemType: 4` → 填空题

---

## 🛠️ 技术栈

- **Python 3.10+**
- [openai](https://pypi.org/project/openai/) — AI API 调用
- [httpx](https://pypi.org/project/httpx/) — HTTP 请求（API 拉取）
- 无其它第三方依赖

---

## 📄 License

MIT