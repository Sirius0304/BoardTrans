# -*- coding: utf-8 -*-
"""翻译字数上限回归（2026-10-13）

需求：设置里新增「翻译字数上限」，取值 0~1000，**保存后要真正作用于译文**，
而不是只把值存进配置。

分三层验证：
  1. 配置层 —— clamp_translate_chars 的边界行为（0 / 1000 / 负数 / 超范围 / 非数字）
  2. 引擎层 —— 上限真的会截断译文（这是"真正生效"的核心证据）
  3. 装配层 —— service 把配置值传进引擎；config.load 会把手写越界值夹紧
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, engine as engine_mod   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


LONG_ZH = (
    "今天早上我六点半就起床了，因为要赶八点的火车去上海出差。"
    "出门的时候天还没完全亮，路上几乎没有行人，只有环卫工人在清扫街道。"
    "到了火车站，我发现候车大厅里已经挤满了人，看来大家都是赶早班车的。"
    "上车之后我找了一个靠窗的位置坐下，打算趁这两个小时把方案再改一遍。"
    "窗外的风景从城市的高楼慢慢变成了田野和小河，心情也跟着放松了下来。"
)


def main():
    print("=" * 68)
    print("翻译字数上限回归")
    print("=" * 68)

    # ---------- 1. 配置层边界 ----------
    print("\n[1] 配置层：clamp_translate_chars 的边界行为")
    cases = [
        (0, 0, "0 本身合法（=不限制）"),
        (1000, 1000, "1000 本身合法（上限端点）"),
        (-5, 0, "负数夹到 0"),
        (5000, 1000, "超上限夹到 1000"),
        ("300", 300, "数字字符串可解析"),
        ("abc", 1000, "非数字回退默认值"),
        (None, 1000, "None 回退默认值"),
        ("", 1000, "空串回退默认值"),
        (250, 250, "区间内原样返回"),
    ]
    for raw, want, desc in cases:
        got = config.clamp_translate_chars(raw)
        check(f"clamp({raw!r}) -> {want}（{desc}）", got == want, f"实际 {got}")

    print("\n[2] ellipsize 的截断行为")
    check("不超长时原样返回", engine_mod.ellipsize("hello", 10) == "hello")
    r = engine_mod.ellipsize("abcdefghij", 5)
    check("超长时截到 limit-1 + 省略号", r == "abcd…", repr(r))
    check("截断后长度不超过上限", len(r) <= 5, f"len={len(r)}")
    check("limit=1 时只留省略号", engine_mod.ellipsize("abcdef", 1) == "…")
    check("limit<=0 时不截断", engine_mod.ellipsize("abcdef", 0) == "abcdef")
    check("中文按字符数计", len(engine_mod.ellipsize("一二三四五六", 3)) == 3)

    # ---------- 3. 引擎层：真的截断 ----------
    print("\n[3] 引擎层：上限真的作用于译文（核心）")
    eng = engine_mod.TranslationEngine()

    full = eng.translate(LONG_ZH, "en", "fast", max_chars=0)
    check("上限 0 = 不限制，拿到完整译文", len(full) > 200, f"{len(full)} 字")

    for limit in (60, 120, 300):
        out = eng.translate(LONG_ZH, "en", "fast", max_chars=limit)
        ok = len(out) <= limit
        check(f"上限 {limit}：译文不超过 {limit} 字", ok, f"实际 {len(out)} 字")
        check(f"上限 {limit}：被截断时有省略号提示", out.endswith("…"), repr(out[-12:]))

    big = eng.translate(LONG_ZH, "en", "fast", max_chars=100000)
    check("上限远大于自然长度时不多加省略号", not big.endswith("…"), f"{len(big)} 字")

    tiny = eng.translate(LONG_ZH, "en", "fast", max_chars=1)
    check("上限 1 时输出就是省略号（仍是合法长度）", len(tiny) <= 1, repr(tiny))

    # 第一句本身就超上限：也必须被压住
    first = "这是一段用来测试单个句子就超过上限的中文文本，里面没有任何标点符号所以不会被切分。" * 2
    one = eng.translate(first, "en", "fast", max_chars=80)
    check("首句即超限时也压到上限内", len(one) <= 80, f"实际 {len(one)} 字")

    print("\n[4] 引擎层：日文方向（上限只作用于最终日文）")
    ja = eng.translate(LONG_ZH, "ja", "fast", max_chars=120)
    check("中日方向：日文译文不超过 120 字", len(ja) <= 120, f"实际 {len(ja)} 字")
    check("中日方向：被截断时有省略号", ja.endswith("…"), repr(ja[-10:]))
    ja_full = eng.translate(LONG_ZH, "ja", "fast", max_chars=0)
    check("中日方向：上限 0 时完整", len(ja_full) > len(ja), f"{len(ja_full)} vs {len(ja)}")

    # ---------- 5. 装配层 ----------
    print("\n[5] 装配层：service 真的把配置值传进引擎")
    import inspect
    from app import service as service_mod
    src = inspect.getsource(service_mod)
    check("service 读取 max_translate_chars", "max_translate_chars" in src)
    check("service 把它传给 engine.translate", "max_chars=limit" in src)
    check("service 用 clamp 夹过一次", "clamp_translate_chars" in src)

    print("\n[6] 配置层：手工写越界值会被 load 夹紧")
    r = _probe_config('{"max_translate_chars": 5000}')
    check("config.json 里写 5000 → 读回 1000", r == "1000", r)
    r2 = _probe_config('{"max_translate_chars": 300}')
    check("config.json 里写 300 → 读回 300（用户值不动）", r2 == "300", r2)
    r3 = _probe_config('{"max_translate_chars": "abc"}')
    check("config.json 里写 abc → 读回默认值 1000", r3 == "1000", r3)
    r4 = _probe_config('{}')
    check("没有该键 → 读回默认值 1000", r4 == "1000", r4)

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


def _probe_config(payload_json: str) -> str:
    """在隔离数据目录里写一份配置，读回来看 max_translate_chars 的最终值。"""
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
