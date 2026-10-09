# -*- coding: utf-8 -*-
"""验证 zh->en->ja 中转链路，并确定 ▁ 还原与 en_ja 的 config 差异。"""
import json
import os
import shutil
import time
import zipfile

import ctranslate2
import sentencepiece

ROOT = r"F:\shunyi\models"
WORK = os.path.join(os.environ["LOCALAPPDATA"], "BoardTransTest")

PKGS = {
    "zh_en": "translate-zh_en-1_9.argosmodel",
    "en_ja": "translate-en_ja-1_1.argosmodel",
}


def find_package_root(tmp):
    """自动探测包内顶层目录（各包命名不一致）。"""
    entries = [e for e in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, e))]
    if len(entries) == 1:
        return os.path.join(tmp, entries[0])
    # 顶层就是内容（没有包裹目录）
    return tmp

CASES = [
    "你好，今天天气不错。",
    "我们明天下午三点开个会，讨论一下项目进度。",
    "这个功能已经开发完了，但是还需要测试。",
    "不好意思，我刚才没听清楚，你能再说一遍吗？",
    "请把这份文件打印出来，放在我的桌子上。",
    "人工智能正在改变我们的生活方式。",
]


class T:
    def __init__(self, name):
        pkg_file = PKGS[name]
        out = os.path.join(WORK, name)
        if not os.path.exists(os.path.join(out, "model", "model.bin")):
            tmp = os.path.join(WORK, "_tmp_" + name)
            shutil.rmtree(tmp, ignore_errors=True)
            with zipfile.ZipFile(os.path.join(ROOT, pkg_file)) as z:
                z.extractall(tmp)
            src = find_package_root(tmp)
            shutil.rmtree(out, ignore_errors=True)
            shutil.move(src, out)
            shutil.rmtree(tmp, ignore_errors=True)
        self.dir = out
        cfg_path = os.path.join(out, "model", "config.json")
        if os.path.exists(cfg_path):
            cfg = json.load(open(cfg_path, encoding="utf-8"))
        else:
            cfg = {}
        print(f"[{name}] model config: {cfg if cfg else '(无 config.json，用默认值)'}")
        self.add_bos = bool(cfg.get("add_source_bos", False))
        self.add_eos = bool(cfg.get("add_source_eos", True))
        self.sp = sentencepiece.SentencePieceProcessor(model_file=os.path.join(out, "sentencepiece.model"))
        t0 = time.time()
        self.m = ctranslate2.Translator(os.path.join(out, "model"), device="cpu", compute_type="int8")
        print(f"[{name}] 加载 {time.time()-t0:.2f}s  (add_bos={self.add_bos}, add_eos={self.add_eos})")

    def tr(self, text, beam=1):
        toks = self.sp.encode(text, out_type=str)
        if self.add_bos:
            toks = ["<s>"] + toks
        if self.add_eos:
            toks = toks + ["</s>"]
        t0 = time.time()
        res = self.m.translate_batch([toks], beam_size=beam)
        dt = time.time() - t0
        hyp = [t for t in res[0].hypotheses[0] if t not in ("</s>", "<s>")]
        raw = self.sp.decode(hyp)
        clean = raw.replace("\u2581", " ").strip()
        return clean, raw, dt


def main():
    os.makedirs(WORK, exist_ok=True)
    e = T("zh_en")
    j = T("en_ja")

    print("\n=== 中→英（beam=1 / beam=8）===")
    for c in CASES:
        en1, raw, dt1 = e.tr(c, 1)
        en8, _, dt8 = e.tr(c, 8)
        print(f"  原文: {c}")
        print(f"    快(beam1): {en1}   [{dt1*1000:.0f}ms]")
        print(f"    优(beam8): {en8}   [{dt8*1000:.0f}ms]")
        print(f"    raw(未清理): {raw!r}")

    print("\n=== 中→日（zh→en→ja 中转, 各 beam=1）===")
    for c in CASES:
        en, _, _ = e.tr(c, 1)
        ja, _, dt = j.tr(en, 1)
        print(f"  {c}\n    -> {en}\n    -> {ja}   [{dt*1000:.0f}ms]")

    print("\n=== en_ja 单独质量抽查 ===")
    for en in ["Hello, nice day.", "We'll have a meeting tomorrow at 3:00 to discuss the project progress.",
               "I'm sorry, I didn't hear you. Can you repeat that?"]:
        ja1, _, _ = j.tr(en, 1)
        ja8, _, _ = j.tr(en, 8)
        print(f"  {en}\n    beam1: {ja1}\n    beam8: {ja8}")


if __name__ == "__main__":
    main()
