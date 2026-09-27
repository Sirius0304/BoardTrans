# -*- coding: utf-8 -*-
"""定位中→日链路耗时异常的原因。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ctranslate2            # noqa: E402
import sentencepiece          # noqa: E402

from app.engine import TranslationEngine    # noqa: E402

eng = TranslationEngine()
t0 = time.time()
e = eng.load_model("zh_en")
print(f"加载 zh_en: {time.time()-t0:.2f}s")
t0 = time.time()
j = eng.load_model("en_ja")
print(f"加载 en_ja: {time.time()-t0:.2f}s")

print("\n--- 分段计时（重复 3 次）---")
for i in range(3):
    t0 = time.time()
    en = e.translate_text("你好，今天天气不错。", 1)
    t1 = time.time() - t0
    t0 = time.time()
    ja = j.translate_text(en, 3)
    t2 = time.time() - t0
    print(f"  zh_en {t1*1000:6.0f} ms | en_ja {t2*1000:6.0f} ms | {en} | {ja}")

print("\n--- 直接调用 ctranslate2（不经过封装）---")
sp = sentencepiece.SentencePieceProcessor(model_file=os.path.join(j.root, "sentencepiece.model"))
m = ctranslate2.Translator(os.path.join(j.root, "model"), device="cpu", compute_type="int8")
for i in range(3):
    toks = sp.encode("Hello, it is a nice day.", out_type=str) + ["</s>"]
    t0 = time.time()
    m.translate_batch([toks], beam_size=3, length_penalty=1.0, max_decoding_length=256, max_input_length=200)
    print(f"  raw ct2: {(time.time()-t0)*1000:.0f} ms")

print("\n--- 封装里带 max_decoding_length / max_input_length 的调用 ---")
for i in range(3):
    t0 = time.time()
    out = j.translate("Hello, it is a nice day.", beam_size=3)
    print(f"  wrapped: {(time.time()-t0)*1000:.0f} ms  -> {out}")

print("\n--- 不带那两个参数 ---")
for i in range(2):
    toks = sp.encode("Hello, it is a nice day.", out_type=str) + ["</s>"]
    t0 = time.time()
    m.translate_batch([toks], beam_size=3)
    print(f"  raw 无长度参数: {(time.time()-t0)*1000:.0f} ms")
