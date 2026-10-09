# -*- coding: utf-8 -*-
"""安装并实测中日直连大模型 (opus-mt-tc-big-zh-ja)。"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ctranslate2              # noqa: E402
import sentencepiece            # noqa: E402

from app import paths                                   # noqa: E402
from app.engine import MarianTranslator                 # noqa: E402

SRC = os.path.join(os.environ["LOCALAPPDATA"], "BoardTransTest", "tcbig_zh_ja")
DST = os.path.join(paths.packages_dir(), "tcbig_zh_ja")

CASES = [
    "你好，今天天气不错。",
    "我们明天下午三点开个会，讨论一下项目进度。",
    "这个功能已经开发完了，但是还需要测试。",
    "不好意思，我刚才没听清楚，你能再说一遍吗？",
    "请把这份文件打印出来，放在我的桌子上。",
    "人工智能正在改变我们的生活方式。",
    "在全球化背景下，越来越多的企业开始重视跨文化沟通能力，因为这直接影响海外市场的拓展效率。",
]


def install():
    if os.path.exists(os.path.join(DST, "model.bin")):
        print("已安装，跳过复制")
    else:
        os.makedirs(DST, exist_ok=True)
        for name in os.listdir(SRC):
            src_file = os.path.join(SRC, name)
            dst_file = os.path.join(DST, name)
            if not os.path.exists(dst_file):
                t0 = time.time()
                shutil.copy2(src_file, dst_file)
                print(f"  复制 {name} ({os.path.getsize(src_file)/1024/1024:.1f} MB, {time.time()-t0:.1f}s)")

    # CTranslate2 加载 Marian 模型需要一个"按 id 排序的词表数组"文件，
    # 而 HF 仓库给的是 {token: id} 形式的 vocab.json，这里做一次转换。
    shared = os.path.join(DST, "shared_vocabulary.json")
    vocab = os.path.join(DST, "vocab.json")
    if not os.path.exists(shared) and os.path.exists(vocab):
        with open(vocab, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        size = max(mapping.values()) + 1
        tokens = [""] * size
        for token, idx in mapping.items():
            tokens[idx] = token
        with open(shared, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False)
        print(f"  已生成 shared_vocabulary.json（{size} 个词）")


def main():
    install()
    print("\nconfig.json 内容:")
    with open(os.path.join(DST, "config.json"), "r", encoding="utf-8") as f:
        print(" ", f.read().strip())

    print("\n=== 直接使用 MarianTranslator 封装 ===")
    t0 = time.time()
    tr = MarianTranslator(DST, threads=0, compute_type="int8", name="tcbig_zh_ja")
    print(f"  加载耗时 {time.time()-t0:.2f}s  (add_bos={tr.add_bos}, add_eos={tr.add_eos}, sp={os.path.basename(tr.sp_source)})")

    # 预热
    tr.translate("测试", beam_size=2)

    for beam in (2, 4):
        print(f"\n--- beam={beam} ---")
        total = 0.0
        for case in CASES:
            t0 = time.time()
            out = tr.translate(case, beam_size=beam, length_penalty=1.0)
            dt = time.time() - t0
            total += dt
            print(f"  [{dt*1000:5.0f}ms] {case}\n           -> {out}")
        print(f"  合计 {total:.2f}s，平均 {total/len(CASES)*1000:.0f} ms")

    print("\n=== 与英文中转方案对比（同一句）===")
    zh_en = MarianTranslator(os.path.join(paths.packages_dir(), "zh_en"), name="zh_en")
    en_ja = MarianTranslator(os.path.join(paths.packages_dir(), "en_ja"), name="en_ja")
    for case in CASES[:4]:
        t0 = time.time()
        en = zh_en.translate(case, beam_size=1)
        ja_pivot = en_ja.translate(en, beam_size=8, length_penalty=1.0)
        t_pivot = time.time() - t0
        t0 = time.time()
        ja_direct = tr.translate(case, beam_size=4)
        t_direct = time.time() - t0
        print(f"  原文: {case}")
        print(f"    中转 ({t_pivot*1000:.0f}ms): {ja_pivot}")
        print(f"    直连 ({t_direct*1000:.0f}ms): {ja_direct}")


if __name__ == "__main__":
    main()
