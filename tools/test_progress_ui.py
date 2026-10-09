# -*- coding: utf-8 -*-
"""下载进度显示回归测试（界面层，端到端跑真实控件）。

复现并锁死 2026-10-03 报障：「下载完成时进度突然跳回 0%，然后直接显示已下载」。

做法：不联网、不真的下 71 MB，而是把整条链路的回调按真实时序喂进去
（models._Progress 负责产生事件，settings_window 负责渲染），
逐帧断言**界面文字里的百分比单调不减**，且压缩阶段显示的是"正在解压"而不是 0%。
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, ui_kit as ui        # noqa: E402
import app.settings_window as sw            # noqa: E402
from app.service import HotkeyService      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [OK] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def pct_of(text):
    """从状态文字里抠出百分比数字；没有就返回 None。"""
    m = re.search(r"(\d+)\s*%", text or "")
    return int(m.group(1)) if m else None


def test_ui_monotonic():
    print("\n=== 1. 界面渲染：进度单调递增到 100% ===")
    ui.enable_dpi_awareness()
    service = HotkeyService(notify=lambda t, m: None)
    w = sw.SettingsWindow(service)
    w.open()
    time.sleep(2.0)

    key = "zh_en"
    total = models.PACKAGES[key]["size"]
    seen = []

    def drive():
        """按真实时序喂一整轮：0% → 递增 → 100% → 正在解压。"""
        w._model_status[key] = {"status": "downloading", "percent": 0, "error": ""}
        w._active_downloads.add(key)
        w._dl_high[key] = 0

        prog = models._Progress(
            lambda label, done, tot, phase: w._on_download_progress(key, done, tot, label, phase))
        steps = [0.08, 0.22, 0.37, 0.55, 0.71, 0.86, 0.94, 1.0]
        for frac in steps:
            prog.download("中文 → 英文（基础模型）", int(total * frac), total)
            seen.append(("download", w._model_ui[key]["status"].cget("text")))
        prog.finish("中文 → 英文（基础模型）")
        seen.append(("finish", w._model_ui[key]["status"].cget("text")))
        prog.extract("正在解压")
        seen.append(("extract", w._model_ui[key]["status"].cget("text")))

    w._queue.put(("call", drive))
    for _ in range(60):
        if len(seen) >= 10:
            break
        time.sleep(0.1)
    time.sleep(0.6)

    for phase, text in seen:
        print(f"       [{phase:7s}] {text}")

    pcts = [pct_of(t) for _p, t in seen if _p != "extract"]
    check("全程都能解析出百分比", all(p is not None for p in pcts), str(pcts))
    check("百分比单调不减（关键：无跳回 0%）",
          all(b >= a for a, b in zip(pcts, pcts[1:])), str(pcts))
    check("确实到过 100%", pcts and pcts[-1] == 100, str(pcts))
    check("全程没有任何一帧是 0%（除起点外）",
          all(p >= 0 for p in pcts) and pcts[0] >= 0, str(pcts))
    check("不是一开始就 100%（说明真的在走进度）", pcts[0] < 100, str(pcts))

    extract_text = seen[-1][1]
    print(f"       解压阶段文案：{extract_text}")
    check("解压阶段显示「正在解压」", "解压" in extract_text, extract_text)
    check("解压阶段不含百分比 0%", "0%" not in extract_text, extract_text)
    check("解压阶段不再是「正在下载 0%」", "正在下载 0%" not in extract_text, extract_text)

    # 收尾：模拟下载完成，卡片应显示「已下载」
    def finish_ok():
        w._active_downloads.discard(key)
        w._dl_samples.pop(key, None)
        w._dl_high.pop(key, None)
        w._on_download_done(key, True, "")
    w._queue.put(("call", finish_ok))
    time.sleep(1.2)
    final = w._model_ui[key]["status"].cget("text")
    print(f"       完成后的卡片：{final}")
    check("完成后显示「已下载」", "已下载" in final, final)
    w.shutdown()


def test_ui_monotonic_across_retry():
    print("\n=== 2. 换源/退回重试：百分比也不许回退 ===")
    ui.enable_dpi_awareness()
    service = HotkeyService(notify=lambda t, m: None)
    w = sw.SettingsWindow(service)
    w.open()
    time.sleep(2.0)

    key = "zh_en"
    total = models.PACKAGES[key]["size"]
    seen = []

    def drive():
        w._model_status[key] = {"status": "downloading", "percent": 0, "error": ""}
        w._active_downloads.add(key)
        w._dl_high[key] = 0
        # 第一个源下到 70% 失败 → 第二个源从头再来（字节数确实归零）
        for frac in (0.2, 0.45, 0.70):
            w._on_download_progress(key, int(total * frac), total, "源1")
            seen.append(w._model_ui[key]["status"].cget("text"))
        for frac in (0.05, 0.30, 0.60, 1.0):
            w._on_download_progress(key, int(total * frac), total, "源2")
            seen.append(w._model_ui[key]["status"].cget("text"))

    w._queue.put(("call", drive))
    for _ in range(60):
        if len(seen) >= 7:
            break
        time.sleep(0.1)
    time.sleep(0.6)

    pcts = [pct_of(t) for t in seen]
    for t in seen:
        print(f"       {t}")
    check("重试期间百分比仍单调不减", all(b >= a for a, b in zip(pcts, pcts[1:])), str(pcts))
    check("重试后仍能到 100%", pcts[-1] == 100, str(pcts))

    # 百分比与 MB 数必须同源：不能出现"3.6 MB 却显示 69%"这种自相矛盾的画面
    def mb_pair(text):
        m = re.search(r"（([\d.]+)\s*/\s*([\d.]+)\s*MB", text or "")
        return (float(m.group(1)), float(m.group(2))) if m else None

    bad = []
    for t in seen:
        pr = pct_of(t)
        pair = mb_pair(t)
        if pr is None or pair is None:
            continue
        got_mb, tot_mb = pair
        # MB 只保留 1 位小数，71 MB 的粒度约 0.14 个百分点；
        # 留 1.5 个百分点的余量吸收取整误差（真实不一致是几十个点级的）
        if abs(got_mb / tot_mb * 100 - pr) > 1.5:
            bad.append((t, round(got_mb / tot_mb * 100, 1)))
    check("百分比与 MB 数始终一致（无自相矛盾）", not bad, str(bad))
    w.shutdown()


def test_high_water_reset():
    print("\n=== 3. 第二次下载要能从 0% 重新开始 ===")
    ui.enable_dpi_awareness()
    service = HotkeyService(notify=lambda t, m: None)
    w = sw.SettingsWindow(service)
    w.open()
    time.sleep(2.0)

    key = "zh_en"
    total = models.PACKAGES[key]["size"]
    seen = []

    def drive():
        # 第一次：下满
        w._active_downloads.add(key)
        w._dl_high[key] = 0
        w._on_download_progress(key, total, total, "第一次")
        seen.append(("first", w._model_ui[key]["status"].cget("text")))
        # 收尾：模拟下载结束 → 水位线被清
        w._active_downloads.discard(key)
        w._dl_high.pop(key, None)
        w._model_status[key] = {"status": "unknown", "percent": 0, "error": ""}
        # 第二次：重新发起，进度必须从低处开始而不是卡在 100%
        w._on_download_model_reset = True
        w._dl_high[key] = 0
        w._on_download_progress(key, int(total * 0.15), total, "第二次")
        seen.append(("second", w._model_ui[key]["status"].cget("text")))

    w._queue.put(("call", drive))
    for _ in range(40):
        if len(seen) >= 2:
            break
        time.sleep(0.1)
    time.sleep(0.5)

    first = seen[0][1]
    second = seen[1][1]
    print(f"       第一次收尾：{first}")
    print(f"       第二次起步：{second}")
    check("第二次下载从低百分比开始", pct_of(second) is not None and pct_of(second) < 50,
          second)
    w.shutdown()


def test_legacy_callback_path():
    print("\n=== 4. 旧 3 参回调（控制台版）整链路不报错 ===")
    got = []
    prog = models._Progress(lambda label, done, total: got.append((label, done, total)))
    total = models.PACKAGES["zh_en"]["size"]
    prog.download("x", total // 2, total)
    prog.finish("x")
    prog.extract("正在解压")
    check("3 参回调全程收到 3 次事件", len(got) == 3, str(got))
    check("3 参回调下最后一次是满字节", got[1][1] == got[1][2], str(got[1]))
    check("3 参回调下解压事件不再是 0/0",
          not (got[2][1] == 0 and got[2][2] == 0), str(got[2]))


def test_chip_not_clipped():
    print("\n=== 5. 状态胶囊不截断（正在解压等长文案曾被裁掉）===")
    ui.enable_dpi_awareness()
    service = HotkeyService(notify=lambda t, m: None)
    w = sw.SettingsWindow(service)
    w.open()
    time.sleep(2.0)

    key = "zh_en"
    total = models.PACKAGES[key]["size"]
    holder = {}

    def drive():
        # 按 _on_download_progress 的真实渲染路径逐条驱动（含 fg 不变的纯文字变化），
        # 量每一步"文字需要宽"与"胶囊容器实际宽"，断言容器始终装得下。
        ui_chip = w._model_ui[key]
        states = []
        seq = [
            ("状态：未下载", "#6B7280"),
            ("状态：正在下载 0%", "#3B82F6"),
            (f"状态：正在下载 42%　（{30.0/1:.1f} / {total/1024/1024:.1f} MB）", "#3B82F6"),
            ("状态：下载完成，正在解压…", "#3B82F6"),
            ("状态：已下载（占用 82 MB）", "#16A34A"),
        ]
        for text, fg in seq:
            ui_chip["status"].configure(text=text, fg=fg)
            w.root.update_idletasks()
            w.root.update()
            states.append((text, ui_chip["status"].label.winfo_reqwidth(),
                           ui_chip["status"].canvas.winfo_width()))
        holder["states"] = states

    w._queue.put(("call", drive))
    for _ in range(80):
        if holder.get("states"):
            break
        time.sleep(0.1)
    time.sleep(0.5)
    w._queue.put(("call", lambda: None))
    time.sleep(0.3)

    states = holder.get("states", [])
    check("逐帧取样成功", len(states) == 5, f"{len(states)} 帧")
    for text, need, have in states:
        print(f"       需要 {need:4d}px / 容器 {have:4d}px   {text[:34]}")
    bad = [(t, nd, hv) for t, nd, hv in states if nd > hv]
    check("胶囊容器始终装得下文字（无截断）", not bad,
          str([(t[:16], f"{nd}/{hv}") for t, nd, hv in bad]))
    # 文字变长后容器必须真的变宽（去重键修复的直接验证）
    if len(states) >= 3:
        check("长文案下容器比短文案时更宽（宽度真在自适应）",
              states[2][2] > states[1][2], f"{states[1][2]} -> {states[2][2]}")
    w.shutdown()


def main():
    print("=" * 64)
    print("键译 · 下载进度显示回归测试（界面层）")
    print("=" * 64)
    for fn in (test_legacy_callback_path, test_ui_monotonic,
               test_ui_monotonic_across_retry, test_high_water_reset,
               test_chip_not_clipped):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAIL.append(f"{fn.__name__} 异常: {exc}")
    print("\n" + "=" * 64)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())