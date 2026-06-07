#!/usr/bin/env python3
"""
build_html.py — 把所有 README.md 渲染成带样式的 HTML（零依赖，纯 Python 解析）。

用法:
  python3 build_html.py            # 生成根 index.html + 各章 index.html + assets/codex.css

这本身也是一个小小的「解析」教学样本：一个够用的 Markdown 子集解析器，
支持 标题 / 段落 / 列表 / 引用 / 分隔线 / 围栏代码块 / 表格 / 行内代码·粗体·斜体·链接
（含引用式链接 [text][ref] 与 [ref] 简写）。

链接重写规则（让 file:// 直接点开也能跳转）：
  foo/            -> foo/index.html
  foo/README.md   -> foo/index.html
  foo.md          -> foo.html
  code.py / *.rs  -> 原样（点开看源码）
  #anchor         -> 原样（标题会带上同样规则生成的 id）
"""

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ─────────────────────────────────────────────────────────────
#  行内：代码、粗体、斜体、链接（含引用式）
# ─────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """GitHub 风格锚点：小写、去标点、空白转连字符（保留中文/数字/字母）。"""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)      # \w 在 Python str 正则里默认含 Unicode（中文）
    s = re.sub(r"\s+", "-", s)
    return s


def rewrite_href(href: str) -> str:
    if href.startswith(("#", "http://", "https://", "mailto:")):
        return href
    base, sep, anchor = href.partition("#")
    if base.endswith("/"):
        base += "index.html"
    elif base.endswith("README.md"):
        base = base[: -len("README.md")] + "index.html"
    elif base.endswith(".md"):
        base = base[:-3] + ".html"
    return base + (("#" + anchor) if anchor else "")


def render_inline(text: str, refs: dict[str, str]) -> str:
    # 1) 抽出行内代码，先占位（内容单独转义），避免后续规则破坏它
    spans: list[str] = []

    def stash_code(m: re.Match) -> str:
        spans.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash_code, text)

    # 2) 转义其余文本（占位符 \x00 不受影响）
    text = html.escape(text)

    # 3) 行内链接 [t](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                  lambda m: f'<a href="{rewrite_href(m.group(2))}">{m.group(1)}</a>', text)

    # 4) 引用式链接 [t][ref]
    def ref_full(m: re.Match) -> str:
        url = refs.get(m.group(2).lower())
        return f'<a href="{rewrite_href(url)}">{m.group(1)}</a>' if url else m.group(0)

    text = re.sub(r"\[([^\]]+)\]\[([^\]]+)\]", ref_full, text)

    # 5) 引用式简写 [ref]（仅当 ref 已定义）
    def ref_short(m: re.Match) -> str:
        url = refs.get(m.group(1).lower())
        return f'<a href="{rewrite_href(url)}">{m.group(1)}</a>' if url else m.group(0)

    text = re.sub(r"\[([^\]]+)\]", ref_short, text)

    # 6) 粗体、斜体
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)

    # 7) 还原行内代码
    text = re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)
    return text


# ─────────────────────────────────────────────────────────────
#  块级解析
# ─────────────────────────────────────────────────────────────

def collect_refs(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    """抽出引用式链接定义 `[ref]: url`，返回 (定义表, 去掉定义行后的正文)。"""
    refs, kept = {}, []
    for line in lines:
        m = re.match(r"\s*\[([^\]]+)\]:\s*(\S+)\s*$", line)
        if m:
            refs[m.group(1).lower()] = m.group(2)
        else:
            kept.append(line)
    return refs, kept


def md_to_html(md: str) -> str:
    refs, lines = collect_refs(md.split("\n"))
    out: list[str] = []
    i, n = 0, len(lines)

    def inline(t: str) -> str:
        return render_inline(t, refs)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 原样透传块级 HTML（<details>/<summary>/<!-- -->），让折叠块在 HTML 中生效
        if stripped.startswith("<") and stripped.endswith(">") \
                and re.match(r"</?[a-zA-Z!]", stripped):
            out.append(line)
            i += 1
            continue

        # 围栏代码块
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束围栏
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(buf))}</code></pre>")
            continue

        # 标题
        m = re.match(r"(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            out.append(f'<h{level} id="{slugify(text)}">{inline(text)}</h{level}>')
            i += 1
            continue

        # 分隔线
        if re.match(r"^(\s*[-*_]){3,}\s*$", line) and stripped in ("---", "***", "___",
                                                                  "----", "*****"):
            out.append("<hr>")
            i += 1
            continue

        # 表格：当前行是 |...| 且下一行是分隔行
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]) \
                and "-" in lines[i + 1]:
            header = _split_row(line)
            i += 2  # 跳过表头与分隔行
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            out.append(_render_table(header, rows, inline))
            continue

        # 引用块
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{inline(' '.join(b for b in buf if b.strip()))}</blockquote>")
            continue

        # 列表（有序 / 无序，连续行）
        if re.match(r"^\s*([-*]|\d+\.)\s+", line):
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < n and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                item = re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i])
                items.append(f"<li>{inline(item)}</li>")
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 段落（吃到空行或下一个块）
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")

    return "\n".join(out)


def _starts_block(line: str) -> bool:
    s = line.strip()
    return (s.startswith(("```", "#", ">")) or re.match(r"^\s*([-*]|\d+\.)\s+", line)
            or ("|" in line and s.startswith("|")))


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _render_table(header: list[str], rows: list[list[str]], inline) -> str:
    thead = "".join(f"<th>{inline(h)}</th>" for h in header)
    body = ""
    for r in rows:
        cells = "".join(f"<td>{inline(c)}</td>" for c in r)
        body += f"<tr>{cells}</tr>"
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"


# ─────────────────────────────────────────────────────────────
#  页面模板 + 样式
# ─────────────────────────────────────────────────────────────

CSS = """\
:root{--fg:#1b1f23;--bg:#fff;--muted:#57606a;--border:#d0d7de;--accent:#0969da;
  --code-bg:#f6f8fa;--pre-bg:#0d1117;--pre-fg:#e6edf3;--star:#bf8700}
*{box-sizing:border-box}
body{margin:0;color:var(--fg);background:var(--bg);
  font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.topbar{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--border);
  padding:12px 24px;font-weight:600}
.topbar a{color:var(--accent);text-decoration:none}
main{max-width:860px;margin:0 auto;padding:32px 24px 80px}
h1,h2,h3,h4{line-height:1.3;margin:1.6em 0 .6em;font-weight:700}
h1{font-size:1.9em;border-bottom:1px solid var(--border);padding-bottom:.3em}
h2{font-size:1.45em;border-bottom:1px solid var(--border);padding-bottom:.3em}
h3{font-size:1.2em}
a{color:var(--accent)}
p,li{color:var(--fg)}
code{background:var(--code-bg);padding:.15em .35em;border-radius:6px;font-size:.9em;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--pre-bg);color:var(--pre-fg);padding:16px;border-radius:10px;overflow:auto;
  font-size:.86em;line-height:1.55}
pre code{background:none;padding:0;color:inherit;font-size:1em}
blockquote{margin:1em 0;padding:.4em 1em;border-left:4px solid var(--accent);
  background:var(--code-bg);color:var(--muted);border-radius:0 8px 8px 0}
table{border-collapse:collapse;width:100%;margin:1.2em 0;font-size:.93em;display:block;overflow:auto}
th,td{border:1px solid var(--border);padding:8px 12px;text-align:left;vertical-align:top}
th{background:var(--code-bg)}
tr:nth-child(even) td{background:#fafbfc}
hr{border:0;border-top:1px solid var(--border);margin:2em 0}
details{border:1px solid var(--border);border-radius:10px;padding:.5em 1em;margin:1em 0;background:#fcfcfd}
details[open]{padding-bottom:1em}
summary{cursor:pointer;font-weight:600;color:var(--accent);padding:.2em 0}
.think{background:#fff8e6;border:1px solid #f0d68a;border-radius:10px;padding:.5em 1.2em;margin:1.5em 0}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;margin:1.4em 0}
.card{border:1px solid var(--border);border-radius:10px;padding:14px 16px;text-decoration:none;color:inherit}
.card:hover{border-color:var(--accent);box-shadow:0 2px 10px rgba(0,0,0,.06)}
.card .n{font-weight:700;font-size:1.05em}
.card .d{color:var(--muted);font-size:.9em;margin-top:4px}
footer{max-width:860px;margin:0 auto;padding:0 24px 60px;color:var(--muted);font-size:.85em}
"""


def page(title: str, body: str, depth: int, is_en: bool = False) -> str:
    prefix = "../" * depth
    css = f"{prefix}assets/codex.css"
    home = f"{prefix}" + ("README.en.html" if is_en else "index.html")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{css}">
</head>
<body>
<div class="topbar"><a href="{home}">📘 learn-codex</a></div>
<main class="md">
{body}
</main>
<footer>本页由 <code>build_html.py</code> 从 Markdown 渲染生成。</footer>
</body>
</html>
"""


def first_heading(md: str) -> str:
    for line in md.split("\n"):
        m = re.match(r"#\s+(.*)$", line)
        if m:
            return re.sub(r"[*`]", "", m.group(1)).strip()
    return "learn-codex"


# ─────────────────────────────────────────────────────────────
#  主流程
# ─────────────────────────────────────────────────────────────

def build() -> None:
    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "assets" / "codex.css").write_text(CSS, encoding="utf-8")

    targets: list[tuple[Path, int]] = []
    for root_readme in (ROOT / "README.md", ROOT / "README.en.md"):   # 中 + 英 落地页
        if root_readme.exists():
            targets.append((root_readme, 0))
    for chapter_readme in sorted(ROOT.glob("s*/README.md")) + sorted(ROOT.glob("s*/README.en.md")):
        targets.append((chapter_readme, 1))
    for doc in sorted(ROOT.glob("docs/*.md")):          # 深入长文（glob 同时含 *.en.md）
        targets.append((doc, 1))

    for src, depth in targets:
        md = src.read_text(encoding="utf-8")
        is_en = src.name.endswith(".en.md")
        out_html = page(first_heading(md), md_to_html(md), depth, is_en)
        # README.md → 同目录 index.html；其余（README.en.md / docs 文章）→ 同名 .html
        out_path = src.parent / "index.html" if src.name == "README.md" else src.with_suffix(".html")
        out_path.write_text(out_html, encoding="utf-8")
        print(f"  {src.relative_to(ROOT)} -> {out_path.relative_to(ROOT)}")

    print(f"✅ 生成 {len(targets)} 个页面 + assets/codex.css")
    print(f"   打开: file://{(ROOT / 'index.html')}")


if __name__ == "__main__":
    build()
