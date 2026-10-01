"""把 BP 的 Markdown 导出成网页版 HTML 和打印版 PDF。

    python tools/export_bp.py BP_Waywise_2026-09-28.md                    # → 同名 .html 和 .pdf
    python tools/export_bp.py BP_Waywise_2026-09-28.md --join "五、,十、"   # 这些章节接在上一章后面

样式沿用 BP_Mumi_2026-09-22.html 的网页样式；打印时另加一层更紧凑的样式：
第一页只放标题和「一页纸」，之后每一章新起一页。PDF 用本机 Chrome 无头打印。
依赖：pip install markdown
"""

import argparse
import re
import subprocess
from pathlib import Path

import markdown
from markdown.extensions.toc import slugify_unicode

ROOT = Path(__file__).resolve().parent.parent
STYLE_SOURCE = ROOT / "BP_Mumi_2026-09-22.html"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 小标题（###）：沿用文档自己的配色 —— 深绿字（同章节标题）+ 左侧一条橙红细线（同眉标、引用块的点缀色），
# 让一章里的几个部分一眼能分开。网页版和 PDF 都生效
H3_CSS = """
h3{color:var(--forest); font-size:20px; font-weight:700; padding-left:11px;
   border-left:3px solid var(--signal); line-height:1.35; margin:40px 0 14px}
/* 插图：<figure class="fig">（单张，可加 narrow）、<figure class="fig row">（几张并排）*/
figure.fig{margin:22px 0 26px; break-inside:avoid; page-break-inside:avoid}
figure.fig img{display:block; max-width:100%; height:auto; border-radius:10px; border:1px solid var(--rule)}
figure.fig.narrow img{max-width:62%; margin:0 auto}
figure.fig.row .imgs{display:flex; gap:12px; align-items:flex-start}
figure.fig.row .imgs img{flex:1 1 0; min-width:0}
figure.fig figcaption{font-size:13px; color:var(--ink-faint); text-align:center; margin-top:8px}
"""

# 只在打印（导出 PDF）时生效，不影响网页版
PRINT_CSS = """
@page{size:A4; margin:19mm 19mm 19mm 19mm}
@media print{
  html,body{-webkit-print-color-adjust:exact; print-color-adjust:exact}
  html,body,.shell,main,.masthead{background:#fff !important}
  body{font-size:13.4px; line-height:1.66}
  main{padding:0}

  /* 第一页：标题和开头几句是重点，字号大一些；不要日期、粗横线和分隔线 */
  .masthead{margin:0; padding:0; border-bottom:0}
  .masthead .eyebrow{margin:0 0 6px}
  .masthead h1{font-size:38px; margin:0 0 8px}
  .masthead .standfirst{font-size:16px; line-height:1.6; color:var(--ink)}
  .masthead .meta{display:none}
  .masthead + p{margin-top:16px; font-size:17px}
  .masthead + p + p{font-size:15px}
  figure.fig{margin:12px 0 16px}
  figure.fig figcaption{font-size:11px; margin-top:5px}
  hr{display:none}

  /* 每一章新起一页；「一页纸」留在第一页；标了 join 的章节接在上一章后面 */
  h2{font-size:21px; margin:0 0 12px; padding-top:0; border-top:0; break-before:page; page-break-before:always}
  main > h2:first-of-type{break-before:auto; page-break-before:auto; margin-top:18px}
  h2.join{break-before:auto; page-break-before:auto; margin-top:84px}  /* 同一页的两章之间多空两行 */
  h2,h3{break-after:avoid; page-break-after:avoid}
  h3{font-size:17px; margin:22px 0 9px; padding-left:9px}
  h2 + h3{margin-top:4px}
  p,ul,ol{max-width:none}
  p{margin:0 0 8px}
  li{margin:0 0 5px}

  .tablewrap{overflow:visible; margin:0 0 12px}
  table{font-size:12.2px; line-height:1.5}
  th,td{padding:5px 9px}
  th{font-size:11.5px}
  main > h2:first-of-type + .tablewrap table{font-size:12.6px; line-height:1.55}
  main > h2:first-of-type + .tablewrap td{padding:7px 10px}
  /* 表格、列表、代码块、引用都比一页短：整块挪到下一页，不从中间断开 */
  tr,pre,blockquote,ul,ol,.tablewrap{break-inside:avoid; page-break-inside:avoid}
  /* 引出列表 / 表格 / 代码块的那句话，跟着它们走，不单独留在上一页底部 */
  p:has(+ ul), p:has(+ ol), p:has(+ .tablewrap), p:has(+ pre){break-after:avoid; page-break-after:avoid}
  blockquote{margin:0 0 12px; padding:9px 14px; font-size:13px}
  pre{margin:0 0 12px; padding:10px 14px; font-size:12px; line-height:1.55;
      background:#F0F0EC; color:#222; box-shadow:none; border:1px solid #DDD}
}
"""

# 默认每章新起一页；--join 指定哪些章节接在上一章后面（按章节标题开头匹配，如 "五、,十、"）


def render_html(md_path: Path, title: str, standfirst: str, date: str, join: tuple[str, ...] = ()) -> str:
    old = STYLE_SOURCE.read_text(encoding="utf-8")
    head = old[: old.index("<body")]
    head = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", head)
    head = head.replace("</style>", H3_CSS + PRINT_CSS + "</style>", 1)

    lines = md_path.read_text(encoding="utf-8").split("\n")
    body_md = "\n".join(lines[1:])  # 第一行 # 标题放进 masthead
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc"],
        extension_configs={"toc": {"slugify": slugify_unicode, "toc_depth": "2"}},
    )
    html = md.convert(body_md)
    html = re.sub(r"<thead>\s*<tr>\s*(?:<th></th>\s*)+</tr>\s*</thead>\s*", "", html)  # 没有列名的表格不要空表头
    html = html.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>")
    for prefix in join:
        html = re.sub(rf'<h2 id="([^"]*)">{re.escape(prefix)}', rf'<h2 id="\1" class="join">{prefix}', html)
    toc = "".join(f'<a href="#{t["id"]}" class="lv2">{t["name"]}</a>' for t in md.toc_tokens)
    return f"""{head}<body>
<div class="shell">
<nav class="toc"><p class="toc-label">目录</p>{toc}</nav>
<main>
<header class="masthead">
  <p class="eyebrow">商业计划书</p>
  <h1>{lines[0].lstrip("# ").strip()}</h1>
  <p class="standfirst">{standfirst}</p>
  <p class="meta">{date}</p>
</header>
{html}
</main>
</div>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("md", type=Path)
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--date", default="")
    ap.add_argument("--join", default="", help='接在上一章后面的章节，逗号分隔，如 "五、,十、"')
    ap.add_argument("--standfirst", default="AI 时代，背下来的东西都不再稀缺。稀缺的是一个会提问的孩子，和一个知道自己在做什么的家长。")
    args = ap.parse_args()

    md_path = args.md.resolve()
    html_path = md_path.with_suffix(".html")
    pdf_path = (args.pdf or md_path.with_suffix(".pdf")).resolve()
    date = args.date or re.search(r"\d{4}-\d{2}-\d{2}", pdf_path.name or md_path.name).group(0)
    join = tuple(j.strip() for j in args.join.split(",") if j.strip())
    html_path.write_text(render_html(md_path, "Waywise 商业计划书", args.standfirst, date, join), encoding="utf-8")
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=15000",
         f"--print-to-pdf={pdf_path}", html_path.as_uri()],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"{html_path.name}\n{pdf_path.name}")


if __name__ == "__main__":
    main()
