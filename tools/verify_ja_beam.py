# -*- coding: utf-8 -*-
"""测试 en_ja 模型在不同 beam_size 下的稳定性，确定"高速度档"的最小可用 beam。"""
import os

import ctranslate2
import sentencepiece

WORK = os.path.join(os.environ["LOCALAPPDATA"], "ShunYiTest")
JA = os.path.join(WORK, "en_ja")

SENTS = [
    "Hello, it's a nice day.",
    "We'll have a meeting tomorrow at 3 p.m. to discuss the progress of the project.",
    "This is already developed, but it needs to be tested.",
    "I'm sorry, I didn't hear you. Can you say that again?",
    "Please print it out and put it on my desk.",
    "Artificial intelligence is changing our way of life.",
    "Could you please help me check whether this document is complete?",
]


def main():
    sp = sentencepiece.SentencePieceProcessor(model_file=os.path.join(JA, "sentencepiece.model"))
    m = ctranslate2.Translator(os.path.join(JA, "model"), device="cpu", compute_type="int8")

    for beam in (1, 2, 3, 4, 6, 8):
        print(f"\n================ beam = {beam} ================")
        import time
        total = 0.0
        for s in SENTS:
            toks = sp.encode(s, out_type=str) + ["</s>"]
            t0 = time.time()
            res = m.translate_batch([toks], beam_size=beam)
            total += time.time() - t0
            out = sp.decode([t for t in res[0].hypotheses[0] if t != "</s>"]).replace("\u2581", " ").strip()
            print(f"  {s}\n    -> {out}")
        print(f"  7 句合计耗时 {total:.2f}s")


if __name__ == "__main__":
    main()
