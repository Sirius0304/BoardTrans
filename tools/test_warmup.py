# -*- coding: utf-8 -*-
"""模型预热（2026-10-16 提速方案②）回归

要保证三件事：
  1. 引擎侧接口正确：keys_for / is_ready_for / ensure_loaded
     —— 中→英只需 zh_en；中→日需 zh_en + en_ja（经英文中转）
  2. 预热**必须**在取词之前触发（否则并行无从谈起，等于没改）
  3. 预热**不能阻塞**工作线程：耗时的 ensure_loaded 只能在后台线程里调用

真机耗时对照见 tools/diag_warmup.py（实测：串行 0.47s → 并行 0.11s，
取词窗口内模型已就绪，翻译阶段省 0.36s）。
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import engine as engine_mod   # noqa: E402
from app import service as service_mod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    print("=" * 68)
    print("模型预热（方案②）回归")
    print("=" * 68)

    print("\n[1] 引擎接口：keys_for / is_ready_for / ensure_loaded")
    check("keys_for('en') 只要 zh_en",
          engine_mod.TranslationEngine.keys_for("en") == ["zh_en"],
          str(engine_mod.TranslationEngine.keys_for("en")))
    check("keys_for('ja') 要 zh_en + en_ja（中转）",
          engine_mod.TranslationEngine.keys_for("ja") == ["zh_en", "en_ja"],
          str(engine_mod.TranslationEngine.keys_for("ja")))

    eng = engine_mod.TranslationEngine()
    loaded = []
    eng.load_model = lambda key: loaded.append(key)      # 只记账，不真读盘

    check("未加载时 is_ready_for 为假", eng.is_ready_for("en") is False)
    eng.ensure_loaded("ja")
    check("ensure_loaded('ja') 依次加载两个模型",
          loaded == ["zh_en", "en_ja"], str(loaded))

    loaded.clear()
    eng._models = {"zh_en": object(), "en_ja": object()}
    check("都已加载后 is_ready_for 为真", eng.is_ready_for("ja") is True)
    check("都已加载后 is_ready_for('en') 也为真", eng.is_ready_for("en") is True)

    print("\n[2] service：预热必须在取词之前，且不阻塞工作线程")
    ssrc = inspect.getsource(service_mod)
    handler = inspect.getsource(service_mod.HotkeyService._handle_translate)
    i_warm = handler.find("_warmup_engine_async()")
    i_cap = handler.find("capture_selected_text(")
    check("_handle_translate 里调用了预热", i_warm != -1, str(i_warm))
    check("预热排在取词之前（并行才有意义）",
          i_warm != -1 and i_cap != -1 and i_warm < i_cap,
          f"warmup@{i_warm} < capture@{i_cap} = {i_warm < i_cap}")

    warm = inspect.getsource(service_mod.HotkeyService._warmup_engine_async)
    check("_warmup_engine_async 先判 is_ready_for（已就绪就零成本返回）",
          "is_ready_for" in warm)
    check("_warmup_engine_async 用后台线程承载加载", "threading.Thread" in warm)
    check("_warmup_engine_async 自身不直接加载模型（不阻塞）",
          "ensure_loaded" not in warm.split("def _warmup_job")[0])

    job = inspect.getsource(service_mod.HotkeyService._warmup_job)
    check("真正的加载放在 _warmup_job 里", "ensure_loaded" in job)
    check("预热失败不影响翻译（有兜底）", "except Exception" in job)

    print("\n[3] 老接口未被破坏")
    check("preload() 仍存在（启动/切语言用）", "def preload" in ssrc)
    check("preload() 改为复用 ensure_loaded", "ensure_loaded(target)" in ssrc)
    sig = inspect.signature(service_mod.HotkeyService.preload)
    check("preload 签名不变", list(sig.parameters) == ["self"], str(list(sig.parameters)))

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
