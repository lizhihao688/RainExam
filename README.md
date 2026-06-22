# ![](https://www.yuketang.cn/static/images/favicon.ico)RainExam

**雨课堂在线考题提取 & AI 自动解答工具**

一键拉取雨课堂在线试卷，提取为格式化文本，并支持通过 AI 自动解答。

---

## ✨ 功能

| 功能 | 说明 |
|------|------|
| 📥 **在线拉取** | 从雨课堂在线 API 直接拉取试卷 JSON |
| 🔄 **题型支持** | 选择题 (A/B/C/D)、判断题 (T/F)、填空题 |
| 🤖 **AI 解答** | 调用任意 OpenAI 兼容 API 自动答题 + 解析 |
| 📋 **答案速查** | 每题答案内联标注 + 文件末尾速查表 |
| 📄 **分组输出** | 每 N 题一个 TXT 文件（默认 50 题/组） |
| ⚙️ **配置省心** | 通过 `.env` 文件管理配置，无需每次敲参数 |

---

## 📥 环境准备（新手必看）

> 本工具支持 **Windows 10/11** 和 **macOS**。以下会分别说明两个系统的安装步骤。

### 1️⃣ 安装 Python

RainExam 需要 **Python 3.10 或更高版本**。

<details>
<summary><b>🪟 Windows 用户点我</b></summary>

1. 打开浏览器，访问 [python.org/downloads](https://www.python.org/downloads/)
2. 点击黄色的 **Download Python 3.12.x**（或更高版本）按钮
3. 下载完成后，**双击**安装包
4. **⚠️ 重要**：安装时 **务必勾选** 下方的 `Add Python to PATH`（添加到环境变量）
5. 点击 **Install Now**，等待安装完成
6. 验证安装：
   - 按 `Win + R`，输入 `cmd` 回车
   - 在命令行中输入以下命令，看到版本号即表示成功：
   ```
   python --version
   pip --version
   ```
</details>

<details>
<summary><b>🍎 macOS 用户点我</b></summary>

**方法一：使用 Homebrew（推荐）**
```bash
# 如果没有 Homebrew，先安装：https://brew.sh
brew install python@3.12

# 验证
python3 --version
pip3 --version
```

**方法二：官网下载**
1. 访问 [python.org/downloads](https://www.python.org/downloads/)
2. 点击下载 macOS 版的 `.pkg` 安装包
3. 双击安装，一路下一步即可
</details>

### 2️⃣ 安装 uv（推荐，速度更快）

`uv` 是替代 `pip` 的现代化 Python 包管理器，下载依赖速度比 pip 快 10-100 倍。

<details>
<summary><b>🪟 Windows 用户点我</b></summary>

**在 PowerShell 中安装（推荐）：**
1. 按 `Win + X`，选择 **Windows PowerShell** 或 **终端**
2. 粘贴以下命令并回车：
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
3. 安装完成后，**关闭并重新打开** PowerShell
4. 验证安装：
```powershell
uv --version
```

**如果上述方法不行，也可以用 pip 安装：**
```powershell
pip install uv
```
</details>

<details>
<summary><b>🍎 macOS 用户点我</b></summary>

```bash
# 方法一：Homebrew
brew install uv

# 方法二：一键脚本
curl -LsSf https://astral.sh/uv/install.sh | sh

# 验证
uv --version
```
</details>

### 3️⃣ 或者只装 pip（更简单）

如果 uv 安装遇到困难，直接用 Python 自带的 pip 也可以，速度慢一点但不影响使用。

```bash
# Windows
pip install openai httpx

# macOS
pip3 install openai httpx
```

---

## 🚀 快速开始

### 1. 下载项目

**方式一：下载 ZIP（推荐给不熟悉 Git 的同学）**

1. 打开项目页面：https://github.com/lizhihao688/RainExam
2. 点击绿色的 `Code` 按钮 → `Download ZIP`
3. 解压到你的电脑上，比如 `D:\RainExam` 或 `~/Desktop/RainExam`

**方式二：使用 Git**

```bash
git clone https://github.com/lizhihao688/RainExam.git
cd RainExam
```

### 2. 安装依赖

```bash
# 使用 uv（推荐，速度快）
uv sync

# 或使用 pip
pip install openai httpx
```

### 3. 配置 Cookie

```bash
# 复制配置模板
cp .env.example .env
```

**🪟 Windows 注意**：`cp` 命令在 cmd 中不可用，请直接用 **文件资源管理器** 复制 `.env.example` 文件，重命名为 `.env`。

编辑 `.env` 文件（用 **记事本** 或 **VS Code** 打开），填入你的雨课堂 Cookie：

```ini
XT_COOKIE=xt_lang=zh; x_access_token=eyJ0eXAiOi...
```

### 4. 运行

```bash
# 拉取试卷并提取题目
python extract_questions.py --exam-id 4361438

# 拉取 + AI 解答
python extract_questions.py --exam-id 4361438 --answer
```

---

## 📖 详细用法

### 获取 Cookie

1. 打开浏览器（Chrome / Edge 均可），**先登录** 雨课堂
2. 进入考试页面
3. 按 **F12** 打开开发者工具
4. 点击顶部的 **Network（网络）** 标签
5. 按 **F5** 刷新页面
6. 在左侧列表中找到任意一个请求（以 `.json` 或 `show_paper` 开头的）
7. 点击该请求，在右侧找到 **Request Headers（请求头）**
8. 找到 `Cookie:` 那一行，**右键 → Copy Value** 复制完整内容
9. 粘贴到 `.env` 文件中的 `XT_COOKIE=` 后面
![img.png](img.png)
### 基本用法

```bash
python extract_questions.py --exam-id 4361438
```

会自动拉取试卷，生成 `questions_1.txt`、`questions_2.txt` 等文件，保存在当前目录。

### AI 解答

**使用前请先在 `.env` 中配置好 AI 相关参数。**

```bash
# 使用 .env中配置的 api-key 和 model
python extract_questions.py --exam-id 4361438 --answer

# 或在命令中临时指定
python extract_questions.py --exam-id 4361438 --answer \
  --ai-api-key sk-xxx \
  --ai-model qwen-plus
```

AI 会自动识别题型：

| 题型 | 识别方式 | AI 回答格式 |
|------|---------|------------|
| 选择题 | 有选项列表 | 选 A/B/C/D + 解析 |
| 判断题 | `Type: "Judgement"` | 选 T/F + 解析 |
| 填空题 | `Type: "FillBlank"` | 填写文本答案 + 解析 |

---

## ⚙️ 完整配置参考

### `.env` 文件（推荐）

```ini
# 必需：雨课堂 Cookie
XT_COOKIE=xt_lang=zh; x_access_token=eyJ0eXAiOi...

# AI API Key（使用 --answer 时需要，二选一即可）
AI_API_KEY=sk-your-api-key
# OPENAI_API_KEY=sk-your-api-key

# AI 服务地址（根据你的服务商修改）
# 默认值: https://api.openai.com/v1
# DeepSeek:  https://api.deepseek.com/v1
# 通义千问:  https://dashscope.aliyuncs.com/compatible-mode/v1
# 硅基流动:  https://api.siliconflow.cn/v1
AI_BASE_URL=

# AI 模型名称
# 默认值: gpt-4o-mini
AI_MODEL=

# 每组包含的题目数（默认 50）
# PAGE_SIZE=50
```

### 命令行参数

| 参数 | 说明 | 是否必填 |
|------|------|---------|
| `--exam-id <ID>` | 试卷 ID | ✅ 必填 |
| `--answer` / `-a` | 启用 AI 解答 | 可选 |
| `--ai-api-key <key>` | 临时指定 API Key | 可选 |
| `--ai-base-url <url>` | 临时指定 API 地址 | 可选 |
| `--ai-model <name>` | 临时指定模型 | 可选 |

---

## ❓ 常见问题

### ❔ 运行后提示"python 不是内部或外部命令"

👉 **原因**：安装 Python 时没有勾选 `Add Python to PATH`

**解决方法（Windows）：**
1. 打开 **控制面板 → 系统和安全 → 系统 → 高级系统设置**
2. 点击 **环境变量**
3. 在 **系统变量** 中找到 `Path`，双击编辑
4. 添加以下两行（根据你的 Python 安装路径调整）：
   - `C:\Users\你的用户名\AppData\Local\Programs\Python\Python312\`
   - `C:\Users\你的用户名\AppData\Local\Programs\Python\Python312\Scripts\`
5. 点击确定，**重新打开** 命令行

**快速修复**：卸载 Python 重新安装，这次记得勾选 ✅ `Add Python to PATH`

### ❔ `cp .env.example .env` 在 Windows 上报错

👉 **Windows 的 cmd.exe 没有 cp 命令**

**解决方法：**
- 打开文件资源管理器，找到项目文件夹
- 复制 `.env.example` 文件
- 粘贴并重命名为 `.env`
- 用 **记事本** 或 **VS Code** 打开 `.env` 填写配置

### ❔ `uv` 安装失败

👉 直接用 pip 也可以，只是慢一点：

```bash
pip install openai httpx
```

### ❔ Cookie 过期了怎么办？

👉 雨课堂的 Cookie 有效期有限，如果运行后报 401 错误，需要重新从浏览器复制 Cookie。


### ❔ 试卷内容变成乱码？

👉 请确保在 `.env` 文件中使用了正确的 Cookie，且文件保存为 **UTF-8 编码**（记事本另存为时选择 UTF-8）。

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

## 🛠️ 技术栈

- **Python 3.10+** — 无需额外运行时
- [openai](https://pypi.org/project/openai/) — AI API 调用
- [httpx](https://pypi.org/project/httpx/) — HTTP 请求（API 拉取）
- **零外部配置** — 全部依赖通过 pip / uv 自动安装

---

## 📄 License

MIT