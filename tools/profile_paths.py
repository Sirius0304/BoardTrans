# -*- coding: utf-8 -*-
"""逐项测量 TranslationEngine.translate 的内部开销。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import paths                      # noqa: E402
from app.engine import TranslationEngine   # noqa: E402

eng = TranslationEngine()
eng.load_model("zh_en")
eng.load_model("en_ja")


def bench(label, fn, n=5):
    times = []
    for _ in range(n):
        t0 = time.time()
        fn()
        times.append((time.time() - t0) * 1000)
    print(f"  {label:<34} 平均 {sum(times)/len(times):7.1f} ms  (最快 {min(times):.1f})")


print("--- 路径函数 ---")
bench("paths.data_dir()", paths.data_dir, 20)
bench("paths.packages_dir()", paths.packages_dir, 20)
bench("paths.config_path()", paths.config_path, 20)

print("\n--- engine 内部步骤 ---")
bench("is_model_ready('tcbig_zh_ja')", lambda: eng.is_model_ready("tcbig_zh_ja"))
bench("load_model('zh_en') 缓存命中", lambda: eng.load_model("zh_en"))
bench("model_root('zh_en')", lambda: eng.model_root("zh_en"))

print("\n--- 完整调用 ---")
bench("translate -> en/fast", lambda: eng.translate("你好，今天天气不错。", "en", "fast"))
bench("translate -> ja/fast", lambda: eng.translate("你好，今天天气不错。", "ja", "fast"))
print("  翻译结果:", eng.translate("你好，今天天气不错。", "ja", "fast"))

print("\n--- 单独测 get_clipboard / 其他可能的隐藏开销 ---")
try:
    from app import capture
    bench("capture.get_clipboard_text()", capture.get_clipboard_text)
except Exception as exc:  # noqa: BLE001
    print("  跳过:", exc)
