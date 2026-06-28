"""
雨课堂 Quiz 试卷提取模块（与考试 Exam 模式完全不同）

流程:
  1. personal_result API → 获取所有题目的 problem_id + 正确答案 + 用户作答
  2. problem_shape API  → 获取每道题的图片 URL（题干 + 选项均为图片）
  3. 下载图片到本地
  4. 生成 HTML 报告（嵌入图片 + 标注正确答案 / 用户作答）

用法:
    python quiz.py --quiz-id 123456
    python quiz.py --quiz-id 123456 --classroom-id 789
"""

import argparse
import hashlib
import html
import json
import os
import sys
import time
from pathlib import Path

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

QUIZ_API_BASE = "https://www.yuketang.cn"

_QUIZ_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "referer": "https://www.yuketang.cn/",
    "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    ),
    "x-client": "web",
    "xtbz": "cloud",
}


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────


def _parse_cookie(cookie_str: str) -> dict:
    """将 'key=value; key2=value2' 格式的 cookie 解析为字典"""
    result: dict[str, str] = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _problem_type_name(ptype: int) -> str:
    """problem_type 数字 → 可读题型名"""
    return {1: "单选题", 2: "多选题", 3: "判断题", 4: "填空题"}.get(ptype, f"未知({ptype})")


def _safe_filename(text: str) -> str:
    """将文本转为安全的文件名"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:60]


# ──────────────────────────────────────────────
# API 拉取
# ──────────────────────────────────────────────


def fetch_quiz_results(cookie: str, **params) -> dict:
    """
    调用 personal_result API，获取答题结果（含正确答案）

    params: 传给 API 的额外查询参数，常见如 quiz_id、classroom_id
    返回完整响应 dict
    """
    try:
        import httpx
    except ImportError:
        print("错误：需要 httpx 库\n请执行: pip install httpx", file=sys.stderr)
        sys.exit(1)

    url = f"{QUIZ_API_BASE}/v2/api/web/quiz/personal_result"
    print("🌐 正在拉取 Quiz 答题结果...")

    with httpx.Client() as client:
        resp = client.get(
            url,
            headers=_QUIZ_HEADERS,
            cookies=_parse_cookie(cookie),
            params=params,
            follow_redirects=True,
            timeout=30,
        )

    if resp.status_code != 200:
        print(
            f"错误：API 请求失败 (HTTP {resp.status_code})\n"
            f"  响应: {resp.text[:300]}",
            file=sys.stderr,
        )
        sys.exit(1)

    data = resp.json()
    if data.get("errcode", 0) != 0:
        print(f"❌ API 返回错误: {data.get('errmsg', '未知')}", file=sys.stderr)
        sys.exit(1)

    return data


def fetch_problem_shape(cookie: str, problem_id: int, **extra_params) -> dict:
    """
    调用 problem_shape API，获取单道题的图片结构

    extra_params: 额外查询参数（如 classroom_id）
    返回 data 字段（含 Shapes / Bullets / Answer 等）
    """
    try:
        import httpx
    except ImportError:
        print("错误：需要 httpx 库", file=sys.stderr)
        sys.exit(1)

    url = f"{QUIZ_API_BASE}/v2/api/web/quiz/problem_shape"
    params = {"problem_id": problem_id, **extra_params}

    with httpx.Client() as client:
        resp = client.get(
            url,
            headers=_QUIZ_HEADERS,
            cookies=_parse_cookie(cookie),
            params=params,
            follow_redirects=True,
            timeout=30,
        )

    if resp.status_code != 200:
        print(f"  ⚠️  problem_id={problem_id} 请求失败 (HTTP {resp.status_code})")
        return {}

    root = resp.json()
    if root.get("errcode", 0) != 0:
        print(f"  ⚠️  problem_id={problem_id} API 错误: {root.get('errmsg')}")
        return {}

    return root.get("data", {})


# ──────────────────────────────────────────────
# 图片分组（题干 vs 选项）
# ──────────────────────────────────────────────


def classify_shapes(
    shapes: list[dict],
    bullets: list[dict],
) -> tuple[list[dict], dict[str, dict]]:
    """
    根据 Shapes 的 Top 坐标和 Bullets 的位置，将图片分为题干和选项。

    判断逻辑：
      - 第一条线 = 第一个 Bullet 的 Top - Bullet Height 的一半（区分题干 / 选项 A）
      - 后续线 = 相邻 Bullet Top 的中点（区分相邻选项）
      - Top < boundaries[0] → 题干
      - boundaries[i] <= Top < boundaries[i+1] → sorted_bullets[i] 对应的选项

    返回: (question_images, option_images)
      - question_images: 题干图片列表（按 ID 排序）
      - option_images: {"A": shape, "B": shape, ...} 映射
    """
    if not shapes:
        return [], {}

    # 没有 Bullets 信息时，全部作为题干
    if not bullets:
        return sorted(shapes, key=lambda s: s.get("Top", 0)), {}

    # 按 Bullets Top 排序
    sorted_bullets = sorted(bullets, key=lambda b: b["Top"])

    # 分界线策略：
    #   boundaries[0] = first_bullet_top - height/2  （题干 / A 分界线）
    #   boundaries[i] = (bullet[i-1].Top + bullet[i].Top) / 2  （后续分界线）
    #   共 N 条线（N = bullet 数量），最后一条线之后归最后一个选项
    first_bullet_top = sorted_bullets[0]["Top"]
    first_bullet_height = sorted_bullets[0].get("Height", 40)
    boundaries: list[float] = [first_bullet_top - first_bullet_height / 2]

    for i in range(1, len(sorted_bullets)):
        mid = (sorted_bullets[i - 1]["Top"] + sorted_bullets[i]["Top"]) / 2
        boundaries.append(mid)

    question_imgs: list[dict] = []
    option_imgs: dict[str, dict] = {}

    for shape in shapes:
        top = shape.get("Top", 0)
        assigned = False

        for i, boundary in enumerate(boundaries):
            if top < boundary:
                if i == 0:
                    # 在题干/选项分界线之上 → 题干
                    question_imgs.append(shape)
                else:
                    # boundaries[i] 之上 → 属于 sorted_bullets[i-1] 的选项
                    bullet = sorted_bullets[i - 1]
                    label = bullet.get("Label", chr(ord("A") + i - 1))
                    option_imgs[label] = shape
                assigned = True
                break

        if not assigned:
            # 超过所有分界线 → 最后一个选项
            bullet = sorted_bullets[-1]
            label = bullet.get("Label", chr(ord("A") + len(sorted_bullets) - 1))
            option_imgs[label] = shape

    question_imgs.sort(key=lambda s: s.get("Top", 0))
    return question_imgs, option_imgs


# ──────────────────────────────────────────────
# 图片下载
# ──────────────────────────────────────────────


def download_images(
    shapes: list[dict],
    output_dir: str,
    cookie: str,
) -> dict[str, str]:
    """
    批量下载图片到本地，返回 {原始URL: 本地文件名} 映射。

    文件名使用 URL 的 SHA1 短哈希 + .png，避免重名。
    """
    try:
        import httpx
    except ImportError:
        print("错误：需要 httpx 库", file=sys.stderr)
        sys.exit(1)

    img_dir = os.path.join(output_dir, "images")
    os.makedirs(img_dir, exist_ok=True)

    url_map: dict[str, str] = {}
    urls = [s["URL"] for s in shapes if s.get("URL")]

    if not urls:
        return url_map

    with httpx.Client() as client:
        for url in urls:
            short_hash = hashlib.sha1(url.encode()).hexdigest()[:12]
            filename = f"{short_hash}.png"
            local_path = os.path.join(img_dir, filename)

            if os.path.exists(local_path):
                url_map[url] = filename
                continue

            try:
                resp = client.get(url, timeout=30)
                if resp.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                    url_map[url] = filename
                else:
                    print(f"  ⚠️  图片下载失败: HTTP {resp.status_code} — {url[:80]}")
            except Exception as e:
                print(f"  ⚠️  图片下载异常: {e} — {url[:80]}")

            time.sleep(0.1)

    return url_map


# ──────────────────────────────────────────────
# HTML 报告生成
# ──────────────────────────────────────────────


def generate_html_report(
    questions: list[dict],
    output_path: str,
    title: str = "Quiz Report",
) -> str:
    """
    生成 HTML 报告

    questions 每项结构:
    {
        "index": 1,
        "problem_id": 90071851,
        "problem_type": 1,
        "correct_answer": "A",       # 正确答案
        "user_answer": "B",           # 用户作答
        "is_correct": False,
        "question_images": ["img/a.png"],   # 题干图片（本地文件名）
        "option_images": {"A": "img/b.png"}, # 选项图片（本地文件名）
    }
    """
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="UTF-8">',
        f"<title>{html.escape(title)}</title>",
        "<style>",
        "body{font-family:-apple-system,sans-serif;max-width:800px;margin:0 auto;padding:20px;background:#f5f5f5}",
        ".q{background:#fff;border-radius:8px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.1)}",
        ".q-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}",
        ".q-title{font-weight:bold;font-size:15px}",
        ".tag{padding:2px 8px;border-radius:4px;font-size:12px;color:#fff}",
        ".tag-ok{background:#52c41a}.tag-no{background:#ff4d4f}",
        ".tag-type{background:#1890ff;margin-right:6px}",
        ".q-img{margin-bottom:10px}",
        ".q-img img{max-width:100%;height:auto}",
        ".opts{display:flex;flex-direction:column;gap:8px}",
        ".opt{display:flex;align-items:flex-start;gap:8px;padding:6px 10px;border-radius:6px;border:1px solid #e8e8e8}",
        ".opt.correct{border-color:#52c41a;background:#f6ffed}",
        ".opt.wrong{border-color:#ff4d4f;background:#fff2f0}",
        ".opt-label{font-weight:bold;min-width:20px}",
        ".opt img{max-width:100%;height:auto}",
        ".summary{background:#fff;border-radius:8px;padding:16px 20px;margin-top:20px;box-shadow:0 1px 3px rgba(0,0,0,.1)}",
        ".summary table{width:100%;border-collapse:collapse}",
        ".summary th,.summary td{padding:6px 12px;text-align:center;border-bottom:1px solid #f0f0f0}",
        ".summary .correct-cell{color:#52c41a;font-weight:bold}",
        ".summary .wrong-cell{color:#ff4d4f;font-weight:bold}",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>{html.escape(title)}</h1>",
        f"<p>共 {len(questions)} 道题</p>",
    ]

    correct_count = sum(1 for q in questions if q.get("is_correct"))
    parts.insert(
        -1,
        f"<p>得分：{correct_count}/{len(questions)} 正确</p>",
    )

    for q in questions:
        idx = q["index"]
        is_correct = q.get("is_correct", False)
        tag_class = "tag-ok" if is_correct else "tag-no"
        tag_text = "✓ 正确" if is_correct else "✗ 错误"
        type_name = _problem_type_name(q.get("problem_type", 0))

        parts.append('<div class="q">')
        parts.append('<div class="q-head">')
        parts.append(f'<span class="q-title">题目 {idx}</span>')
        parts.append("<span>")
        parts.append(f'<span class="tag tag-type">{type_name}</span>')
        parts.append(f'<span class="tag {tag_class}">{tag_text}</span>')
        parts.append("</span></div>")

        # 题干图片
        for img in q.get("question_images", []):
            parts.append(f'<div class="q-img"><img src="images/{html.escape(img)}"></div>')

        # 选项图片
        correct_ans = str(q.get("correct_answer", ""))
        user_ans = str(q.get("user_answer", ""))
        option_imgs = q.get("option_images", {})

        if option_imgs:
            parts.append('<div class="opts">')
            for label in sorted(option_imgs.keys()):
                img = option_imgs[label]
                css_cls = ""
                if label in correct_ans:
                    css_cls = " correct"
                elif label in user_ans and label not in correct_ans:
                    css_cls = " wrong"
                marker = ""
                if label in correct_ans:
                    marker = " ✓"
                if label in user_ans and label not in correct_ans:
                    marker = " ✗(你的选择)"
                parts.append(
                    f'<div class="opt{css_cls}">'
                    f'<span class="opt-label">{label}{marker}</span>'
                    f'<img src="images/{html.escape(img)}">'
                    f"</div>"
                )
            parts.append("</div>")
        else:
            # 非选择题：直接显示答案
            parts.append(
                f'<div class="q-img">'
                f"<p><b>正确答案：</b>{html.escape(correct_ans)}</p>"
                f"<p><b>你的作答：</b>{html.escape(user_ans)}</p>"
                f"</div>"
            )

        parts.append("</div>")

    # ── 速查表 ──
    parts.append('<div class="summary">')
    parts.append(f"<h3>📋 答案速查（共 {len(questions)} 题）</h3>")
    parts.append("<table><tr><th>题号</th><th>正确答案</th><th>你的作答</th><th>结果</th></tr>")
    for q in questions:
        is_correct = q.get("is_correct", False)
        cell_cls = "correct-cell" if is_correct else "wrong-cell"
        result_text = "✓" if is_correct else "✗"
        parts.append(
            f"<tr>"
            f"<td>{q['index']}</td>"
            f"<td>{html.escape(str(q.get('correct_answer', '')))}</td>"
            f"<td>{html.escape(str(q.get('user_answer', '')))}</td>"
            f'<td class="{cell_cls}">{result_text}</td>'
            f"</tr>"
        )
    parts.append("</table></div>")

    parts.append("</body></html>")

    content = "\n".join(parts)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────


def process_quiz(
    cookie: str,
    output_dir: str,
    title: str = "Quiz Report",
    delay: float = 0.3,
    **api_params,
) -> str:
    """
    完整流程: 拉取答题结果 → 获取每题图片 → 下载 → 生成 HTML 报告

    api_params: 传给 personal_result API 的查询参数（如 quiz_id, classroom_id）
    返回生成的 HTML 文件路径
    """
    # ── 阶段 1: 获取答题结果 ──
    result_data = fetch_quiz_results(cookie, **api_params)
    inner = result_data.get("data", {})

    objective_list = inner.get("objective_result_list", [])
    if not objective_list:
        print("⚠️  未获取到任何客观题结果")
        return ""

    quiz_title = inner.get("title", title)
    print(f"✅ 获取到 {len(objective_list)} 道题（{quiz_title}）")

    # ── 阶段 2: 逐题获取图片并分类 ──
    print(f"\n📖 正在获取题目图片...")
    questions: list[dict] = []

    for item in objective_list:
        problem_id = item.get("problem_id")
        idx = item.get("problem_index", 0)
        if not problem_id:
            continue

        print(f"  [{idx}/{len(objective_list)}] problem_id={problem_id} ...", end=" ")

        shape_data = fetch_problem_shape(cookie, problem_id, **api_params)
        if not shape_data:
            print("跳过")
            questions.append({
                "index": idx,
                "problem_id": problem_id,
                "problem_type": item.get("problem_type", 0),
                "correct_answer": _format_answer(item.get("answer")),
                "user_answer": _format_answer(item.get("result")),
                "is_correct": item.get("correct", False),
                "question_images": [],
                "option_images": {},
            })
            continue

        shapes = shape_data.get("Shapes", [])
        bullets = shape_data.get("Bullets", [])
        q_imgs, o_imgs = classify_shapes(shapes, bullets)
        print(f"题干:{len(q_imgs)} 选项:{len(o_imgs)}")

        questions.append({
            "index": idx,
            "problem_id": problem_id,
            "problem_type": item.get("problem_type", 0),
            "correct_answer": _format_answer(item.get("answer")),
            "user_answer": _format_answer(item.get("result")),
            "is_correct": item.get("correct", False),
            "_question_shapes": q_imgs,
            "_option_shapes": o_imgs,
        })

        time.sleep(delay)

    # ── 阶段 3: 下载所有图片 ──
    print(f"\n📥 正在下载图片...")
    all_shapes: list[dict] = []
    for q in questions:
        all_shapes.extend(q.get("_question_shapes", []))
        all_shapes.extend(q.get("_option_shapes", {}).values())

    url_map = download_images(all_shapes, output_dir, cookie)
    print(f"✅ 下载完成: {len(url_map)}/{len(all_shapes)} 张图片")

    # 将 URL 映射转为本地文件名
    for q in questions:
        q["question_images"] = [
            url_map[s["URL"]]
            for s in q.pop("_question_shapes", [])
            if s.get("URL") in url_map
        ]
        raw_opts = q.pop("_option_shapes", {})
        q["option_images"] = {
            label: url_map[shape["URL"]]
            for label, shape in raw_opts.items()
            if shape.get("URL") in url_map
        }

    # ── 阶段 4: 生成 HTML 报告 ──
    print(f"\n📝 正在生成 HTML 报告...")
    safe_name = _safe_filename(quiz_title)
    html_path = os.path.join(output_dir, f"quiz_{safe_name}.html")
    generate_html_report(questions, html_path, quiz_title)
    print(f"✅ 已保存: {os.path.abspath(html_path)}  共 {len(questions)} 道题")

    return html_path


def _format_answer(val) -> str:
    """将 answer/result 字段统一转为字符串"""
    if val is None:
        return ""
    if isinstance(val, dict):
        # 填空题: {"1": "smartphone"} → "1: smartphone"
        return "; ".join(f"{k}: {v}" for k, v in val.items())
    return str(val)

# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def build_quiz_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="拉取雨课堂 Quiz 试卷并生成图片报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
环境变量配置（也可写在 .env 文件中）:
  XT_COOKIE      雨课堂 Cookie（必需）
  QUIZ_ID        Quiz ID（可代替 --quiz-id 参数）
  CLASSROOM_ID   课堂 ID（可选）

示例:
  python quiz.py --quiz-id 123456
  python quiz.py --quiz-id 123456 --classroom-id 789
  # 或在 .env 中配置 QUIZ_ID=123456 后直接运行:
  python quiz.py
        """,
    )
    parser.add_argument(
        "--quiz-id",
        default=None,
        help="Quiz ID（也可通过 QUIZ_ID 环境变量设置）",
    )
    parser.add_argument(
        "--classroom-id",
        default=None,
        help="课堂 ID（也可通过 CLASSROOM_ID 环境变量设置）",
    )
    return parser


def main():
    from extract_questions import load_env_file

    load_env_file()

    parser = build_quiz_parser()
    args = parser.parse_args()

    # Cookie: 环境变量
    cookie = os.environ.get("XT_COOKIE", "").strip().strip("'\"")
    if not cookie:
        print(
            "错误：需要设置 XT_COOKIE 环境变量\n"
            "  export XT_COOKIE='...'\n"
            "  或在 .env 中配置 XT_COOKIE=",
            file=sys.stderr,
        )
        sys.exit(1)

    # quiz_id: CLI > 环境变量
    quiz_id = args.quiz_id or os.environ.get("QUIZ_ID", "").strip()
    if not quiz_id:
        print(
            "错误：需要提供 Quiz ID\n"
            "  python quiz.py --quiz-id 123456\n"
            "  或在 .env 中配置 QUIZ_ID=123456",
            file=sys.stderr,
        )
        sys.exit(1)

    # classroom_id: CLI > 环境变量
    classroom_id = args.classroom_id or os.environ.get("CLASSROOM_ID", "").strip()

    params: dict = {"quiz_id": quiz_id}
    if classroom_id:
        params["classroom_id"] = classroom_id

    output_dir = os.getcwd()
    process_quiz(cookie, output_dir, **params)


if __name__ == "__main__":
    main()
