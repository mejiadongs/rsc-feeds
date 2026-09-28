#!/usr/bin/env python3
"""
rsc_feeds.py — 用 Crossref 替代停更的 RSC 官方 RSS

按 ISSN 从 Crossref 拉取期刊最近 N 天新注册的文章，生成标准 RSS 2.0 文件。
只依赖 Python 3.8+ 标准库，无需 pip 安装任何东西。

用法:
    python rsc_feeds.py                 # 生成 feeds/*.xml
    python rsc_feeds.py --days 14       # 回溯 14 天
    python rsc_feeds.py --out docs      # 输出到 docs/（配合 GitHub Pages）
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from email.utils import format_datetime
from xml.sax.saxutils import escape

# ============ 配置区：按需增删期刊 ============
# key 会成为输出文件名，如 feeds/ee.xml
JOURNALS = {
    "ee":   ("Energy & Environmental Science",     "1754-5692"),
    "ta":   ("Journal of Materials Chemistry A",   "2050-7488"),
    "tc":   ("Journal of Materials Chemistry C",   "2050-7526"),
    "mh":   ("Materials Horizons",                 "2051-6347"),
    "nr":   ("Nanoscale",                          "2040-3364"),
    "cp":   ("Physical Chemistry Chemical Physics","1463-9076"),
    "sc":   ("Chemical Science",                   "2041-6520"),
}

# Crossref 建议留联系邮箱，可进入更稳定的 "polite pool"
MAILTO = os.environ.get("CROSSREF_MAILTO", "your_email@example.com")
# =============================================

API = "https://api.crossref.org/journals/{issn}/works"
UA = f"rsc-feeds/1.0 (mailto:{MAILTO})"


def fetch_works(issn, since, rows=200, retries=3):
    """拉取某 ISSN 自 since 以来新创建的记录（按创建时间倒序）。"""
    params = {
        "filter": f"from-created-date:{since.isoformat()},type:journal-article",
        "sort": "created",
        "order": "desc",
        "rows": str(rows),
        "mailto": MAILTO,
    }
    url = API.format(issn=issn) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["message"]["items"]
        except Exception as e:  # 网络抖动时重试
            if attempt == retries - 1:
                print(f"  [失败] {issn}: {e}", file=sys.stderr)
                return None  # None = 抓取失败（区别于"确实没有新文章"）
            time.sleep(3 * (attempt + 1))


def clean(text):
    """去掉 Crossref 标题/摘要里的 JATS/HTML 标签，保留纯文本。"""
    if not text:
        return ""
    text = re.sub(r"<jats:title>.*?</jats:title>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def to_item(w):
    doi = w.get("DOI", "")
    title = clean((w.get("title") or [""])[0]) or "(no title)"
    authors = ", ".join(
        " ".join(p for p in (a.get("given"), a.get("family")) if p)
        for a in w.get("author", [])
    )
    abstract = clean(w.get("abstract", ""))
    created = w.get("created", {}).get("date-time")
    pub = (dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
           if created else dt.datetime.now(dt.timezone.utc))
    desc = ""
    if authors:
        desc += f"<p><b>Authors:</b> {escape(authors)}</p>"
    if abstract:
        desc += f"<p>{escape(abstract)}</p>"
    desc += f"<p>DOI: {escape(doi)}</p>"
    return {
        "title": title,
        "link": f"https://doi.org/{doi}",
        "guid": doi,
        "pub": pub,
        "desc": desc,
    }


def write_rss(path, title, link, items):
    now = format_datetime(dt.datetime.now(dt.timezone.utc))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f"<title>{escape(title)}</title>",
        f"<link>{escape(link)}</link>",
        f"<description>{escape(title)} (via Crossref)</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
    ]
    for it in items:
        parts += [
            "<item>",
            f"<title>{escape(it['title'])}</title>",
            f"<link>{escape(it['link'])}</link>",
            f'<guid isPermaLink="false">{escape(it["guid"])}</guid>',
            f"<pubDate>{format_datetime(it['pub'])}</pubDate>",
            f"<description><![CDATA[{it['desc']}]]></description>",
            "</item>",
        ]
    parts.append("</channel></rss>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="回溯天数（默认 7）")
    ap.add_argument("--out", default="feeds", help="输出目录（默认 feeds）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    since = dt.date.today() - dt.timedelta(days=args.days)
    all_items = []
    failed = 0

    for key, (name, issn) in JOURNALS.items():
        works = fetch_works(issn, since)
        if works is None:
            # 抓取失败时保留旧文件，避免用空 feed 覆盖
            failed += 1
            print(f"  {key:4s} {name:40s} 抓取失败，保留旧 feed")
            continue
        items = [to_item(w) for w in works]
        items.sort(key=lambda x: x["pub"], reverse=True)
        write_rss(os.path.join(args.out, f"{key}.xml"), f"RSC - {name}",
                  f"https://pubs.rsc.org/en/journals/journalissues/{key}", items)
        for it in items:
            it = dict(it, title=f"[{key.upper()}] {it['title']}")
            all_items.append(it)
        print(f"  {key:4s} {name:40s} {len(items):4d} 篇")
        time.sleep(1)  # 对 Crossref 友好一点

    if failed == len(JOURNALS):
        sys.exit("全部期刊抓取失败，未改动任何文件（检查网络能否访问 api.crossref.org）")

    # 合并源：所有期刊一个 feed
    seen, merged = set(), []
    for it in sorted(all_items, key=lambda x: x["pub"], reverse=True):
        if it["guid"] not in seen:
            seen.add(it["guid"])
            merged.append(it)
    write_rss(os.path.join(args.out, "all.xml"), "RSC - All subscribed journals",
              "https://pubs.rsc.org", merged)
    print(f"完成：{len(merged)} 篇，输出目录 {args.out}/")


if __name__ == "__main__":
    main()
