# -*- coding: utf-8 -*-
"""抓取 Argos Translate 官方包索引，筛选中文相关的语言包，并获取文件体积。"""
import json
import urllib.request

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"


def head_size(url: str):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except Exception as e:  # noqa: BLE001
        return f"HEAD失败: {e}"


def main():
    req = urllib.request.Request(INDEX_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode("utf-8"))

    print(f"索引中共有语言包: {len(data)}\n")
    hits = []
    for p in data:
        fc, tc = p.get("from_code", ""), p.get("to_code", "")
        if fc.startswith("zh") or tc.startswith("zh") or fc == "en" and tc in ("ja", "zh"):
            hits.append(p)

    for p in sorted(hits, key=lambda x: (x.get("from_code", ""), x.get("to_code", ""))):
        link = (p.get("links") or [None])[0]
        size = head_size(link) if link else None
        size_mb = f"{size / 1024 / 1024:.1f} MB" if isinstance(size, int) else size
        print(f"{p.get('from_code'):>6} -> {p.get('to_code'):<6} "
              f"| from={p.get('from_name')} to={p.get('to_name')} "
              f"| pkg_ver={p.get('package_version')} argos_ver={p.get('argos_version')} "
              f"| 大小={size_mb}\n         {link}")


if __name__ == "__main__":
    main()
