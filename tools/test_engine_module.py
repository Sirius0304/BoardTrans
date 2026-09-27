# -*- coding: utf-8 -*-
"""测试 app.engine.TranslationEngine 的各条链路。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import TranslationEngine, split_sentences   # noqa: E402

CASES = [
    "你好，今天天气不错。",
    "我们明天下午三点开个会，讨论一下项目进度。",
    "这个功能已经开发完了，但是还需要测试。",
    "不好意思，我刚才没听清楚，你能再说一遍吗？",
    "请把这份文件打印出来，放在我的桌子上。",
]

LONG = ("在全球化背景下，越来越多的企业开始重视跨文化沟通能力，因为这直接影响海外市场的拓展效率。"
        "同时，人工智能技术的快速发展也在改变传统的工作方式，许多人开始使用各种工具来提高效率！"
        "你觉得这种变化是好事还是坏事？")


def main():
    print("=== split_sentences 测试 ===")
    for s in split_sentences(LONG):
        print("   片段:", s)
    print("   片段数:", len(split_sentences(LONG)))

    eng = TranslationEngine()
    t0 = time.time()
    eng.load_model("zh_en")
    print(f"\n加载 zh_en 用时 {time.time()-t0:.2f}s")

    for target in ("en", "ja"):
        for quality in ("fast", "quality"):
            print(f"\n===== 目标={target} 档位={quality} =====")
            total = 0.0
            for case in CASES:
                t0 = time.time()
                out = eng.translate(case, target, quality)
                dt = time.time() - t0
                total += dt
                print(f"  [{dt*1000:5.0f}ms] {case}\n           -> {out}")
            print(f"  合计 {total:.2f}s，平均 {total/len(CASES)*1000:.0f} ms/句  路线={eng.last_stats.get('route')}")

    print("\n===== 长文本（分句）测试 =====")
    for target in ("en", "ja"):
        for quality in ("fast", "quality"):
            t0 = time.time()
            out = eng.translate(LONG, target, quality)
            print(f"  [{target}/{quality}] {time.time()-t0:.2f}s  {eng.last_stats.get('route')}\n    {out}")


if __name__ == "__main__":
    main()
