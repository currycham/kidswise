"""把 BP 的 Markdown 导出成网页版 HTML 和打印版 PDF。

    python tools/export_bp.py BP_Waywise_2026-09-27.md            # → 同名 .html 和 .pdf
    python tools/export_bp.py BP_Waywise_2026-09-27.md --pdf BP_Waywise_2026-09-28.pdf

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
  hr{display:none}

  /* 每一章新起一页；「一页纸」留在第一页；标了 join 的章节接在上一章后面 */
  h2{font-size:21px; margin:0 0 12px; padding-top:0; border-top:0; break-before:page; page-break-before:always}
  main > h2:first-of-type{break-before:auto; page-break-before:auto; margin-top:18px}
  h2.join{break-before:auto; page-break-before:auto; margin-top:84px}  /* 同一页的两章之间多空两行 */
  h2,h3{break-after:avoid; page-break-after:avoid}
  h3{font-size:15.5px; margin:16px 0 7px}
  p,ul,ol{max-width:none}
  p{margin:0 0 8px}
  li{margin:0 0 5px}

  .tablewrap{overflow:visible; margin:0 0 12px}
  table{font-size:12.2px; line-height:1.5}
  th,td{padding:5px 9px}
  th{font-size:11.5px}
  main > h2:first-of-type + .tablewrap table{font-size:12.6px; line-height:1.55}
  main > h2:first-of-type + .tablewrap td{padding:7px 10px}
  tr,pre,blockquote{break-inside:avoid; page-break-inside:avoid}
  blockquote{margin:0 0 12px; padding:9px 14px; font-size:13px}
  pre{margin:0 0 12px; padding:10px 14px; font-size:12px; line-height:1.55;
      background:#F0F0EC; color:#222; box-shadow:none; border:1px solid #DDD}
}
"""

# 这些章节接在上一章后面（不另起一页）：二+三、六+七、八+九、十一+十二
JOIN_WITH_PREVIOUS = ("三、", "七、", "九、", "十二、")


def render_html(md_path: Path, title: str, standfirst: str, date: str) -> str:
    old = STYLE_SOURCE.read_text(encoding="utf-8")
    head = old[: old.index("<body")]
    head = re.sub(r"<title>.*?</title>", f"<title>{title}</title>", head)
    head = head.replace("</style>", PRINT_CSS + "</style>", 1)

    lines = md_path.read_text(encoding="utf-8").split("\n")
    body_md = "\n".join(lines[1:])  # 第一行 # 标题放进 masthead
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc"],
        extension_configs={"toc": {"slugify": slugify_unicode, "toc_depth": "2"}},
    )
    html = md.convert(body_md)
    html = re.sub(r"<thead>\s*<tr>\s*(?:<th></th>\s*)+</tr>\s*</thead>\s*", "", html)  # 没有列名的表格不要空表头
    html = html.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>")
    for prefix in JOIN_WITH_PREVIOUS:
        html = re.sub(rf'<h2 id="([^"]*)">{prefix}', rf'<h2 id="\1" class="join">{prefix}', html)
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
    ap.add_argument("--standfirst", default="AI 时代，背下来的东西都不再稀缺。稀缺的是一个会提问的孩子，和一个知道自己在做什么的家长。")
    args = ap.parse_args()

    md_path = args.md.resolve()
    html_path = md_path.with_suffix(".html")
    pdf_path = (args.pdf or md_path.with_suffix(".pdf")).resolve()
    date = args.date or re.search(r"\d{4}-\d{2}-\d{2}", pdf_path.name or md_path.name).group(0)
    html_path.write_text(render_html(md_path, "Waywise 商业计划书", args.standfirst, date), encoding="utf-8")
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=15000",
         f"--print-to-pdf={pdf_path}", html_path.as_uri()],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"{html_path.name}\n{pdf_path.name}")


if __name__ == "__main__":
    main()
