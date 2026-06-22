"""
将雨课堂在线 API 拉取的 JSON 题目提取为分组 TXT 文件，并支持 AI 自动解答

用法:
    # 从雨课堂在线 API 拉取并提取
    python extract_questions.py --fetch --exam-id 4361438

    # 拉取 + AI 解答
    python extract_questions.py --fetch --exam-id 4361438 --answer

配置文件:
  项目根目录下的 .env 文件会自动加载
  参考 .env.example 创建你的 .env:
    cp .env.example .env

环境变量配置（也可写在 .env 文件中）:
    AI_API_KEY / OPENAI_API_KEY    AI API Key（--answer 时需要）
    AI_BASE_URL                    AI API 地址（默认: https://api.openai.com/v1）
    AI_MODEL                       AI 模型名称（默认: gpt-4o-mini）
    XT_COOKIE                      雨课堂在线 Cookie（--fetch 模式必需）
"""

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path



# AI 默认配置
DEFAULT_AI_MODEL = "gpt-4o-mini"
DEFAULT_AI_TIMEOUT = 60  # 单次请求超时（秒）


# ──────────────────────────────────────────────
# 配置文件加载
# ──────────────────────────────────────────────


def load_env_file(env_path: str = ".env") -> None:
    """
    加载 .env 文件中的配置项到环境变量

    规则：
    - 跳过空行和 # 注释行
    - 支持 KEY=VALUE 格式
    - VALUE 可选带引号（单引号/双引号均可）
    - 仅当环境变量尚未设置时才写入（不覆盖已有的）
    """
    path = Path(env_path)
    if not path.is_file():
        return

    loaded = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            # 去除可能的引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]

            # 不覆盖已存在的环境变量（shell export 优先级更高）
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1

    if loaded:
        print(f"📋 已从 {path.name} 加载 {loaded} 项配置")


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────


def strip_html(text: str) -> str:
    """去除 HTML 标签、HTML 实体，并去掉首尾空白"""
    # 先解码 HTML 实体（&nbsp; &amp; &lt; 等 → 实际字符）
    text = html.unescape(text)
    # 再去除 HTML 标签
    return re.sub(r"<[^>]+>", "", text).strip()


# ──────────────────────────────────────────────
# 网络拉取（雨课堂在线 API）
# ──────────────────────────────────────────────

XT_API_BASE = "https://examination.xuetangx.com"


def fetch_exam_paper(exam_id: str, cookie: str, output_path: str) -> str:
    """
    从雨课堂在线 API 获取试卷 JSON，保存到本地文件

    返回保存后的文件路径
    """
    url = f"{XT_API_BASE}/exam_room/show_paper?exam_id={exam_id}"

    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9",
        "priority": "u=1, i",
        "referer": f"https://examination.xuetangx.com/exam/{exam_id}?isFrom=2",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "x-client": "web",
        "xtbz": "cloud",
    }

    try:
        import httpx
    except ImportError:
        print(
            "错误：--fetch 模式需要 httpx 库\n请执行: pip install httpx",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"🌐 正在从雨课堂在线 API 拉取试卷 (exam_id={exam_id})...")

    with httpx.Client() as client:
        response = client.get(
            url,
            headers=headers,
            cookies=_parse_cookie(cookie),
            follow_redirects=True,
            timeout=30,
        )

    if response.status_code != 200:
        print(
            f"错误：API 请求失败 (HTTP {response.status_code})\n"
            f"  响应内容: {response.text[:300]}",
            file=sys.stderr,
        )
        sys.exit(1)

    # 写入本地文件
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(response.text)

    print(f"✅ API 拉取成功，已保存到: {output_path}")
    return output_path


def _parse_cookie(cookie_str: str) -> dict:
    """将 'key=value; key2=value2' 格式的 cookie 解析为字典"""
    result: dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result


# ──────────────────────────────────────────────
# 提取题目
# ──────────────────────────────────────────────


def extract_questions(json_path: str) -> list[dict]:
    """
    从 JSON 文件中提取题目列表

    JSON 结构:
    {
        "data": {
            "problems": [
                {
                    "Body": "<p>题目内容</p>",
                    "Options": [
                        {"value": "选项A内容"},
                        {"value": "选项B内容"},
                        ...
                    ]
                }
            ]
        }
    }
    """
    # 1. 读取文件
    with open(json_path, "r", encoding="utf-8") as f:
        root = json.load(f)
    if root.get("errcode")!=0:
        print("❌ API 返回错误: "+root.get("errmsg"))
        print("  请检查 cookie，并更新")
        sys.exit(1)

    # 2. 获取题目列表（空值安全）
    data = root.get("data")
    
    problems = data.get("problems") if data else []
    if problems is None:
        problems = []

    # 3. 提取题目
    all_questions: list[dict] = []
    for problem in problems:
        if problem is None:
            continue

        # 识别题型
        problem_type = problem.get("Type", "")
        is_judgement = problem_type == "Judgement" or problem.get("ProblemType") == 6
        is_fillblank = problem_type == "FillBlank" or problem.get("ProblemType") == 4
        is_shortanswer = problem_type == "ShortAnswer" or problem.get("ProblemType") == 5

        # 提取题干（去 HTML，填空的 [填空1] 保留）
        body = problem.get("Body", "")
        if body is None:
            body = ""
        question_text = strip_html(body)

        # 提取选项
        opt_arr = problem.get("Options")
        option_list: list[dict] = []

        has_options = opt_arr and isinstance(opt_arr, list) and not is_fillblank and not is_shortanswer
        if has_options:
            if is_judgement:
                # 判断题：保留 true/false 语义，映射为 T/F
                for opt in opt_arr:
                    if opt is None:
                        continue
                    raw_key = opt.get("key", "")
                    value = opt.get("value", "") or ""
                    option_text = strip_html(value)

                    # 如果 value 为空，使用默认文本
                    if not option_text:
                        option_text = "正确" if raw_key == "true" else "错误"

                    display_key = "T" if raw_key == "true" else "F"
                    option_list.append({
                        "key": display_key,
                        "value": option_text,
                    })
            else:
                # 选择题：按数组顺序分配 ABCD
                for j, opt in enumerate(opt_arr):
                    if opt is None:
                        continue
                    value = opt.get("value", "")
                    if value is None:
                        value = ""
                    option_text = strip_html(value)

                    option_list.append({
                        "key": chr(ord("A") + j),
                        "value": option_text,
                    })

        # 组装题目
        qtype = (
            "fillblank" if is_fillblank else
            "shortanswer" if is_shortanswer else
            "judgement" if is_judgement else
            "choice"
        )
        all_questions.append({
            "question": question_text,
            "options": option_list,
            "type": qtype,
        })

    return all_questions


# ──────────────────────────────────────────────
# AI 解答
# ──────────────────────────────────────────────


def _build_openai_client(api_key: str, base_url: str | None):
    """构建 OpenAI 客户端（延迟导入，避免无 --answer 时安装 openai）"""
    try:
        from openai import OpenAI
    except ImportError:
        print(
            "错误：使用 --answer 需要安装 openai 包\n"
            "请执行: pip install openai",
            file=sys.stderr,
        )
        sys.exit(1)

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def _build_answer_prompt(question: dict, index: int) -> str:
    """为单道题构造 AI 提示词"""
    lines = [f"题目 {index}: {question['question']}"]
    for opt in question.get("options", []):
        lines.append(f"{opt['key']}. {opt['value']}")

    qtype = question.get("type", "choice")
    if qtype == "judgement":
        lines.append("\n这是一道判断题，请判断对错。以 JSON 格式回复：")
        lines.append('{"answer": "T", "explanation": "简要解释为什么"}')
        lines.append('正确选 T，错误选 F')
    elif qtype == "fillblank":
        lines.append("\n这是一道填空题，请填写 [填空] 处的内容。以 JSON 格式回复：")
        lines.append('{"answer": "你的答案", "explanation": "简要解释"}')
    elif qtype == "shortanswer":
        lines.append("\n这是一道问答题，请根据题目内容作答。以 JSON 格式回复：")
        lines.append('{"answer": "你的完整回答", "explanation": "补充说明或依据"}')
    else:
        lines.append("\n请选择正确的答案，并以 JSON 格式回复（仅输出 JSON，不要其它内容）：")
        lines.append('{"answer": "A", "explanation": "简要解释为什么选这个"}')
    return "\n".join(lines)


def _parse_llm_response(content: str) -> dict | None:
    """从 LLM 响应中提取 JSON 答案"""
    # 尝试直接解析
    content = content.strip()
    # 去掉可能的 markdown 代码块标记
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        result = json.loads(content)
        if "answer" in result:
            return result
    except json.JSONDecodeError:
        pass

    # 尝试用正则提取 JSON 片段（支持 A-D 或 T/F）
    match = re.search(r'\{\s*"answer"\s*:\s*"([A-DFT])"\s*\}', content)
    if match:
        return {"answer": match.group(1), "explanation": ""}

    # 尝试提取答案字母（支持 A-D 或 T/F）
    match = re.search(r'答案[：:]\s*([A-DFT])', content)
    if match:
        # 尝试找解释
        expl_match = re.search(r'(?:解释|原因|解析)[：:]\s*(.+?)(?:$|\n)', content)
        explanation = expl_match.group(1).strip() if expl_match else ""
        return {"answer": match.group(1), "explanation": explanation}

    return None


def answer_questions(
    questions: list[dict],
    api_key: str,
    base_url: str | None = None,
    model: str = DEFAULT_AI_MODEL,
    batch_size: int = 10,
    timeout: int = DEFAULT_AI_TIMEOUT,
) -> list[dict | None]:
    """
    使用 AI 解答所有题目

    返回与 questions 等长的列表，每项为 {"answer": "A", "explanation": "..."} 或 None（失败时）
    """
    client = _build_openai_client(api_key, base_url)
    results: list[dict | None] = [None] * len(questions)
    total = len(questions)

    print(f"\n🤖 正在使用模型 [{model}] 解答 {total} 道题...\n")

    for i, q in enumerate(questions):
        prompt = _build_answer_prompt(q, i + 1)
        progress = f"[{i + 1}/{total}]"

        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512,
                timeout=timeout,
            )

            content = resp.choices[0].message.content or ""
            parsed = _parse_llm_response(content)

            if parsed:
                results[i] = parsed
                opts_str = "  ".join(
                    f"{o['key']}. {o['value']}" for o in q.get("options", [])
                )
                info = f"{q['question']}  {opts_str}" if opts_str else q["question"]
                print(
                    f"  {progress} ✅ {parsed['answer']} - {info}"
                )
                if parsed.get("explanation"):
                    print(f"          💡 {parsed['explanation']}")
            else:
                print(
                    f"  {progress} ⚠️  无法解析 AI 响应，原始内容:\n    {content[:200]}"
                )

        except Exception as e:
            print(f"  {progress} ❌ 请求失败: {e}")

    # 统计
    succeeded = sum(1 for r in results if r is not None)
    print(f"\n📊 解答完成：成功 {succeeded} / {total} 道题")
    return results


# ──────────────────────────────────────────────
# 输出文件
# ──────────────────────────────────────────────


def write_pages(
    all_questions: list[dict],
    output_dir: str,
    exam_id: int,
    answers: list[dict | None] | None = None,
):
    """
    将所有题目写入一个 TXT 文件

    如果提供了 answers，会将答案内联写入每题之后，并在文件末尾生成答案速查表
    """
    total = len(all_questions)
    lines: list[str] = []

    # ── 每题内容 + 答案（如有） ──
    for i, q in enumerate(all_questions):
        idx = i + 1
        a = answers[i] if answers else None

        lines.append(f"题目 {idx}")
        lines.append(q["question"])

        opts = q.get("options", [])
        if opts:
            for opt in opts:
                key = opt["key"]
                val = opt["value"]
                marker = ""
                if a is not None:
                    if a is None:
                        marker = "  ❌"
                    elif a.get("answer") == key:
                        marker = "  ← ✅"
                lines.append(f"{key}. {val}{marker}")

        # 答案行
        lines.append("")
        if a is not None:
            if a is None:
                lines.append("📌 解答失败")
            else:
                answer_text = a["answer"]
                lines.append(f"📌 答案：{answer_text}")
                if a.get("explanation"):
                    lines.append(f"💡 解析：{a['explanation']}")
        lines.append("")

    # ── 文件末尾：答案速查表（如有） ──
    if answers:
        lines.append("═" * 55)
        lines.append(f"📋 答案速查（共 {total} 题）")
        lines.append("═" * 55)

        is_text_answer = any(q.get("type") in ("fillblank", "shortanswer") for q in all_questions)
        if is_text_answer:
            for i, (q, a) in enumerate(zip(all_questions, answers)):
                idx = i + 1
                text = a["answer"] if a is not None else "?"
                lines.append(f"  {idx:>3}: {text}")
        else:
            row_parts: list[str] = []
            items_in_row = 0
            for i, (q, a) in enumerate(zip(all_questions, answers)):
                idx = i + 1
                text = a["answer"] if a is not None else "?"
                row_parts.append(f"{idx:>3}: {text}")
                items_in_row += 1
                if items_in_row >= 5:
                    lines.append("  " + "    ".join(row_parts))
                    row_parts = []
                    items_in_row = 0
            if row_parts:
                lines.append("  " + "    ".join(row_parts))

        lines.append("═" * 55)

    content = "\n".join(lines)
    out_path = os.path.join(output_dir, f"answer_{exam_id}.txt")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  成功写入：{os.path.abspath(out_path)}  共 {total} 道题")


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="拉取雨课堂在线试卷并提取为 TXT，支持 AI 自动解答",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
环境变量配置（推荐方式）:
  AI_API_KEY / OPENAI_API_KEY    AI API Key（--answer 时需要）
  AI_BASE_URL                     AI API 地址（默认: https://api.openai.com/v1）
  AI_MODEL                        模型名称（默认: gpt-4o-mini）
  XT_COOKIE                       雨课堂 Cookie（必需）

示例:
  python extract_questions.py --fetch --exam-id 4361438
  python extract_questions.py --fetch --exam-id 4361438 --answer
  AI_MODEL=deepseek-chat XT_COOKIE='xt_lang=zh; x_access_token=...' \\
    python extract_questions.py --fetch --exam-id 4361438 --answer
        """,
    )

    # ── 数据来源 ──
    parser.add_argument(
        "--fetch",
        action="store_true",
        default=True,
        help="从雨课堂在线 API 拉取试卷",
    )
    parser.add_argument(
        "--exam-id",
        required=True,
        help="试卷 ID（例如 4361438）",
    )

    # ── AI 解答选项 ──
    parser.add_argument(
        "--answer",
        "-a",
        action="store_true",
        help="启用 AI 自动解答",
    )
    parser.add_argument(
        "--ai-model",
        default=None,
        help=f"AI 模型名称（默认: {DEFAULT_AI_MODEL}，也可通过 AI_MODEL 环境变量设置）",
    )
    parser.add_argument(
        "--ai-base-url",
        default=None,
        help="AI API 地址（也可通过 AI_BASE_URL 环境变量设置）",
    )
    parser.add_argument(
        "--ai-api-key",
        default=None,
        help="AI API Key（也可通过 AI_API_KEY 或 OPENAI_API_KEY 环境变量设置）",
    )

    return parser


def resolve_ai_config(args) -> dict:
    """从命令行参数 + 环境变量解析 AI 配置"""
    config = {}

    # API Key: CLI > AI_API_KEY > OPENAI_API_KEY
    config["api_key"] = (
        args.ai_api_key
        or os.environ.get("AI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )

    # Base URL: CLI > AI_BASE_URL > OpenAI 默认
    config["base_url"] = args.ai_base_url or os.environ.get("AI_BASE_URL") or None

    # Model: CLI > AI_MODEL > 默认
    config["model"] = (
        args.ai_model or os.environ.get("AI_MODEL") or DEFAULT_AI_MODEL
    )

    return config


def resolve_cookie_from_env() -> str:
    """从环境变量获取雨课堂在线 Cookie"""
    cookie = os.environ.get("XT_COOKIE", "")
    if not cookie:
        return ""
    return cookie.strip().strip("'\"")


def main():
    # 自动加载 .env 配置文件（如果存在）
    load_env_file()

    parser = build_parser()
    args = parser.parse_args()

    # 确保 Cookie 已配置
    cookie = resolve_cookie_from_env()
    if not cookie:
        print(
            "错误：需要设置 XT_COOKIE 环境变量\n"
            "  export XT_COOKIE='xt_lang=zh; x_access_token=xxx'\n"
            "  也可在 .env 文件中配置 XT_COOKIE=",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        # ── 阶段 0: 从 API 拉取 JSON ──
        json_filename = f"exam_{args.exam_id}.json"
        json_path = os.path.join(os.getcwd(), json_filename)

        fetch_exam_paper(args.exam_id, cookie, json_path)

        # ── 阶段 1: 提取题目 ──
        print("📖 正在提取题目...")
        all_questions = extract_questions(json_path)

        if not all_questions:
            print("⚠️  未提取到任何题目，请检查 JSON 结构是否正确")
            sys.exit(0)

        print(f"✅ 共提取到 {len(all_questions)} 道题\n")

        # ── 阶段 2: AI 解答（可选） ──
        answers: list[dict | None] | None = None
        if args.answer:
            ai_config = resolve_ai_config(args)

            if not ai_config["api_key"]:
                print(
                    "错误：使用 --answer 需要提供 API Key\n"
                    "  方式一：export AI_API_KEY=sk-xxx\n"
                    "  方式二：export OPENAI_API_KEY=sk-xxx\n"
                    "  方式三：--ai-api-key sk-xxx",
                    file=sys.stderr,
                )
                sys.exit(1)

            answers = answer_questions(
                all_questions,
                api_key=ai_config["api_key"],
                base_url=ai_config["base_url"],
                model=ai_config["model"],
            )

        # ── 阶段 3: 写入题目文件（有答案则内联 + 速查表） ──
        output_dir = os.path.dirname(os.path.abspath(json_path))
        label = "含答案的题目文件" if answers else "题目文件"
        print(f"\n📝 正在写入{label}...")
        write_pages(all_questions, output_dir, answers, args.exam_id)

        print(f"\n{'='*50}")
        print(f"✅ 全部完成！共 {len(all_questions)} 道题")
        print(f"   输出目录: {output_dir}")

        if answers:
            success_count = sum(1 for r in answers if r is not None)
            print(f"   AI 解答: {success_count}/{len(answers)} 道题成功")

    except json.JSONDecodeError as e:
        print(f"错误：JSON 解析失败 — {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()