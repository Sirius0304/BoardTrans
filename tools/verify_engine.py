# -*- coding: utf-8 -*-
"""实测 OPUS-MT(zh-en) + CTranslate2 的翻译质量与不同解码参数下的耗时。"""
import os
import shutil
import time
import zipfile

import ctranslate2
import sentencepiece

ROOT = r"F:\瞬译\models"
PKG = os.path.join(ROOT, "translate-zh_en-1_9.argosmodel")
# 关键：运行数据必须放在纯 ASCII 路径，sentencepiece 无法打开含中文的路径
WORK = os.path.join(os.environ["LOCALAPPDATA"], "ShunYiTest")
OUT = os.path.join(WORK, "zh_en")
TMP = os.path.join(WORK, "_tmp_extract")

CASES = [
    "你好，今天天气不错。",
    "我们明天下午三点开个会，讨论一下项目进度。",
    "这个功能已经开发完了，但是还需要测试。",
    "不好意思，我刚才没听清楚，你能再说一遍吗？",
    "请把这份文件打印出来，放在我的桌子上。",
    "人工智能正在改变我们的生活方式。",
    "在全球化背景下，越来越多的企业开始重视跨文化沟通能力，因为这直接影响海外市场的拓展效率。",
]


def ensure_extracted():
    os.makedirs(WORK, exist_ok=True)
    if os.path.exists(os.path.join(OUT, "model", "model.bin")):
        return
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    with zipfile.ZipFile(PKG) as z:
        z.extractall(TMP)
    inner = os.path.join(TMP, "translate-zh_en-1_9")
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    shutil.move(inner, OUT)
    shutil.rmtree(TMP, ignore_errors=True)


def main():
    ensure_extracted()
    print(f"CPU 逻辑核心: {os.cpu_count()}")

    sp = sentencepiece.SentencePieceProcessor(model_file=os.path.join(OUT, "sentencepiece.model"))
    model_dir = os.path.join(OUT, "model")

    print("\n=== 加载模型（含首次量化缓存耗时） ===")
    t0 = time.time()
    tr = ctranslate2.Translator(model_dir, device="cpu", compute_type="int8", inter_threads=1)
    print(f"加载耗时: {time.time() - t0:.2f}s")

    def run(text, beam_size, **kw):
        toks = sp.encode(text, out_type=str) + ["</s>"]
        t0 = time.time()
        res = tr.translate_batch([toks], beam_size=beam_size, **kw)
        dt = time.time() - t0
        return sp.decode(res[0].hypotheses[0]), dt

    print("\n=== 预热（第一次推理总是更慢） ===")
    out, dt = run("测试", 1)
    print(f"  预热输出: {out}  耗时 {dt:.3f}s")

    for label, beam in (("高速度 beam=1", 1), ("中 beam=4", 4), ("高质量 beam=8", 8)):
        print(f"\n=== {label} ===")
        total = 0.0
        for c in CASES:
            out, dt = run(c, beam)
            total += dt
            print(f"  [{dt:5.2f}s] {c}\n            -> {out}")
        print(f"  7 句合计: {total:.2f}s  平均: {total/len(CASES):.2f}s")

    print("\n=== 同句重复 5 次取平均（稳定性 + 缓存效应） ===")
    sample = CASES[1]
    for beam in (1, 4, 8):
        ts = []
        for _ in range(5):
            _, dt = run(sample, beam)
            ts.append(dt)
        print(f"  beam={beam}: 平均 {sum(ts)/len(ts)*1000:.0f} ms  最快 {min(ts)*1000:.0f} ms")


if __name__ == "__main__":
    main()
