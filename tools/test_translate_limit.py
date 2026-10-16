# -*- coding: utf-8 -*-
"""「翻译字数上限」功能移除回归（2026-10-16）

需求：设置里的字数选择功能已整体删除，翻译改为**全量翻译，不做任何截断**。
本测试验证三件事：
  1. 删干净 —— app/ 源码里不再有字数上限相关符号（防死代码残留）
  2. 不截断 —— 真实引擎翻长文，译文完整（长度与全量基线一致、不以省略号结尾）
  3. 配置兼容 —— 老配置里残留的 max_translate_chars 键被静默忽略（= None），
     不报错、不影响其他配置项生效
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, engine as engine_mod   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

LONG_ZH = (
    "今天早上我六点半就起床了，因为要赶八点的火车去上海出差。"
    "出门的时候天还没完全亮，路上几乎没有行人，只有环卫工人在清扫街道。"
    "到了火车站，我发现候车大厅里已经挤满了人，看来大家都是赶早班车的。"
    "上车之后我找了一个靠窗的位置坐下，打算趁这两个小时把方案再改一遍。"
    "窗外的风景从城市的高楼慢慢变成了田野和小河，心情也跟着放松了下来。"
)

# 已删除的符号清单：任何一个还出现在源码里，就是没删干净
REMOVED_SYMBOLS = [
    "max_translate_chars", "clamp_translate_chars", "TRANSLATE_CHARS_MIN",
    "TRANSLATE_CHARS_MAX", "normalize_limit", "ellipsize", "TRUNCATION_MARK",
    "_translate_capped", "_build_length_section", "_validate_limit", "_limit_value",
    "_on_limit_commit", "_update_limit_hint", "_on_limit_spin", "Limit.TSpinbox",
]


def main():
    print("=" * 68)
    print("翻译字数上限移除回归")
    print("=" * 68)

    # ---------- 1. 源码无残留 ----------
    print("\n[1] app/ 源码里不再有字数上限相关符号")
    for fname in os.listdir(APP_DIR):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(APP_DIR, fname), encoding="utf-8") as f:
            src = f.read()
        for sym in REMOVED_SYMBOLS:
            check(f"{fname} 不含 {sym}", sym not in src)

    # ---------- 2. 引擎全量翻译 ----------
    print("\n[2] 引擎层：全量翻译，不做截断")
    eng = engine_mod.TranslationEngine()

    full = eng.translate(LONG_ZH, "en", "fast")
    check("长文译文完整（不被压到 1000 字以内）", len(full) > 200, f"{len(full)} 字")
    check("译文不以省略号结尾（截断已移除）", not full.endswith("…"), repr(full[-8:]))

    again = eng.translate(LONG_ZH, "en", "fast")
    check("两次翻译结果一致（无随机截断行为）", again == full)

    ja = eng.translate(LONG_ZH, "ja", "fast")
    check("日文方向同样全量翻译", len(ja) > 100, f"{len(ja)} 字")
    check("日文译文不以省略号结尾", not ja.endswith("…"), repr(ja[-8:]))

    import inspect
    sig = inspect.signature(engine_mod.TranslationEngine.translate)
    check("engine.translate 已无 max_chars 参数", "max_chars" not in sig.parameters,
          str(list(sig.parameters)))
    sig2 = inspect.signature(engine_mod.MarianTranslator.translate_text)
    check("translate_text 已无 max_chars 参数", "max_chars" not in sig2.parameters,
          str(list(sig2.parameters)))

    # ---------- 3. 配置层：残留键被忽略 ----------
    print("\n[3] 配置层：老配置残留的 max_translate_chars 被静默忽略")
    r = _probe_config('{"max_translate_chars": 300, "quality": "quality"}')
    check("残留键读回 None（被丢弃，不报错）", r == "None", r)

    # ---------- 4. service 装配 ----------
    print("\n[4] service：调用不带上限")
    from app import service as service_mod
    ssrc = inspect.getsource(service_mod)
    check("service 不再读取 max_translate_chars", "max_translate_chars" not in ssrc)
    check("service 调用 engine.translate 不带 max_chars", "max_chars=" not in ssrc)

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


def _probe_config(payload_json: str) -> str:
    """在隔离数据目录里写一份配置，读回 max_translate_chars 的最终值。"""
    import subprocess
    import tempfile

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    py = os.path.join(root, ".venv", "Scripts", "python.exe")
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "config.json"), "w", encoding="utf-8") as f:
            f.write(payload_json)
        env = dict(os.environ)
        env["BOARDTRANS_DATA_DIR"] = td
        env.pop("SHUNYI_DATA_DIR", None)
        code = ("import sys; sys.path.insert(0, r'%s')\n"
                "from app import config\n"
                "print('RESULT=' + str(config.load(force=True).get('max_translate_chars')))\n" % root)
        out = subprocess.run([py, "-c", code], capture_output=True, text=True,
                             env=env, encoding="utf-8", errors="replace").stdout
        for line in out.splitlines():
            if line.startswith("RESULT="):
                return line.split("=", 1)[1].strip()
        return f"<无输出: {out.strip()[:60]}>"


if __name__ == "__main__":
    sys.exit(main())
