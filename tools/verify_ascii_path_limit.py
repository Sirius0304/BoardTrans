# -*- coding: utf-8 -*-
"""
关键前提验证：模型放在**含中文的路径**下，引擎还能不能工作？

背景：项目约定里写着"数据目录必须纯 ASCII，sentencepiece 打不开中文路径"。
用户希望把模型改到"软件所在目录"下，而本机软件目录是 F:\\瞬译（含中文）。
如果这条限制仍然存在，直接改路径会导致翻译崩溃 —— 必须先实测。
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import paths   # noqa: E402
import ctranslate2      # noqa: E402
import sentencepiece    # noqa: E402

SRC = os.path.join(paths.packages_dir(), "zh_en")   # 现有的中译英模型（ASCII 路径下）

TARGETS = [
    ("纯英文路径", os.path.join(tempfile.gettempdir(), "shunyi_ascii_test")),
    ("含中文路径", r"F:\瞬译\models\_pathtest"),
    ("含中文+空格", r"F:\瞬译\测试 目录\_pathtest"),
]


def test_one(label, dst):
    print(f"\n  ---- {label} ----")
    print(f"  路径: {dst}")
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    try:
        shutil.copytree(SRC, dst)
    except Exception as exc:  # noqa: BLE001
        print(f"  [跳过] 复制失败: {type(exc).__name__} {exc}")
        return None

    sp_path = os.path.join(dst, "sentencepiece.model")
    model_dir = os.path.join(dst, "model")

    # 1) 分词器能不能打开
    sp_ok = False
    try:
        sp = sentencepiece.SentencePieceProcessor(model_file=sp_path)
        tokens = sp.encode("你好，今天天气不错。", out_type=str)
        sp_ok = len(tokens) > 0
        print(f"  sentencepiece: {'成功' if sp_ok else '空结果'}"
              f"（切出 {len(tokens)} 个 token）")
    except Exception as exc:  # noqa: BLE001
        print(f"  sentencepiece: **失败** {type(exc).__name__}: {str(exc)[:70]}")

    # 2) 引擎能不能加载 + 翻译
    engine_ok = False
    try:
        t0 = time.time()
        tr = ctranslate2.Translator(model_dir, device="cpu", compute_type="int8")
        print(f"  ctranslate2.Translator: 加载成功（{(time.time()-t0)*1000:.0f} ms）")
        if sp_ok:
            toks = sp.encode("你好，今天天气不错。", out_type=str) + ["</s>"]
            out = tr.translate_batch([toks], beam_size=1)
            # 注意：token 列表要用 decode_pieces（decode 是给 id 列表用的）
            text = sp.decode_pieces([t for t in out[0].hypotheses[0] if t != "</s>"])
            engine_ok = "nice day" in text.lower()
            print(f"  实际翻译: {text}  {'[通过]' if engine_ok else '[结果异常]'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ctranslate2: **失败** {type(exc).__name__}: {str(exc)[:70]}")

    shutil.rmtree(dst, ignore_errors=True)
    return {"sp": sp_ok, "engine": engine_ok}


def main():
    print("=" * 74)
    print("  验证：模型放在含中文的路径下能否工作")
    print("=" * 74)
    print(f"  源模型: {SRC}")
    if not os.path.isdir(SRC):
        print("  !! 源模型不存在，无法测试")
        return 1

    results = {}
    for label, dst in TARGETS:
        results[label] = test_one(label, dst)

    print()
    print("=" * 74)
    print("  结论")
    print("=" * 74)
    for label, r in results.items():
        if r is None:
            print(f"  {label}: 未测")
        else:
            verdict = "可用" if (r["sp"] and r["engine"]) else "**不可用**"
            print(f"  {label}: sentencepiece={r['sp']}, 引擎={r['engine']}  → {verdict}")

    ascii_ok = results.get("纯英文路径") and results["纯英文路径"]["engine"]
    cn_bad = results.get("含中文路径") and not results["含中文路径"]["engine"]
    print()
    if cn_bad:
        print("  → **确认限制仍然存在**：模型不能放在含中文的路径下，")
        print("    所以'改到软件目录'这个需求，在软件目录含中文时必须回退到 ASCII 路径。")
    elif ascii_ok and results.get("含中文路径") and results["含中文路径"]["engine"]:
        print("  → 好消息：含中文路径也能正常工作，限制已不再存在（可能是库升级了）。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
