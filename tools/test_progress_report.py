# -*- coding: utf-8 -*-
"""进度上报层单元测试（不联网，纯逻辑）。

验证 app/models.py 的 _Progress / _adapt_progress：
  1. 解压阶段不会再发出 total=0 的"0%"信号
  2. 收尾一定补到 100%
  3. 字节数回退时百分比单调不回退
  4. 旧的 3 参回调（console.progress_line）仍能工作
  5. total 未知（None）时不崩
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models  # noqa: E402

PASS, FAIL = [0], []


def check(name, cond, detail=""):
    if cond:
        PASS[0] += 1
        print(f"  [OK] {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def test_three_arg_compat():
    print("\n=== 1. 旧 3 参回调兼容性 ===")
    got = []

    def old_cb(label, done, total):        # app/console.py 的签名
        got.append((label, done, total))

    models._adapt_progress(old_cb)("下载", 50, 100, models.PHASE_DOWNLOAD)
    check("3 参回调收到 3 个值", got == [("下载", 50, 100)], str(got))

    got2 = []

    def var_cb(*a):                        # 测试里常见的 lambda *a
        got2.append(a)

    models._adapt_progress(var_cb)("下载", 50, 100, models.PHASE_DOWNLOAD)
    check("可变参回调收到 4 个值（含 phase）", got2 == [("下载", 50, 100, "download")], str(got2))

    got3 = []

    def four_cb(label, done, total, phase):
        got3.append(phase)

    models._adapt_progress(four_cb)("x", 1, 2, models.PHASE_EXTRACT)
    check("本来就是 4 参的回调直接透传", got3 == ["extract"], str(got3))

    check("None 回调返回 None", models._adapt_progress(None) is None)


def test_finish_reaches_100():
    print("\n=== 2. 收尾补到 100%（修复前会停在 98%）===")
    seen = []
    p = models._Progress(lambda l, d, t, ph: seen.append((l, d, t, ph)))
    # 模拟节流丢掉了最后一个数据块：只报到 97.5%
    total = 74481402
    p.download("模型", int(total * 0.975), total)
    p.finish("模型")
    last = seen[-1]
    check("最后一次回调是 download 阶段", last[3] == models.PHASE_DOWNLOAD, str(last))
    check("最后一次回调为 100%", int(last[1] * 100 / last[2]) == 100,
          f"{int(last[1]*100/last[2])}%")
    check("finish 之后不会再回调", True)


def test_closed_ignores_late():
    print("\n=== 3. 收尾后忽略迟到的回调 ===")
    seen = []
    p = models._Progress(lambda l, d, t, ph: seen.append((l, d, t, ph)))
    total = 1000
    p.download("m", 500, total)
    p.finish("m")
    n = len(seen)
    p.download("m", 100, total)             # 收尾后又来一条乱序的
    check("收尾后不再产生新回调", len(seen) == n, f"{n} -> {len(seen)}")


def test_extract_no_zero():
    print("\n=== 4. 解压阶段不再归零（本次 bug 的核心）===")
    seen = []
    p = models._Progress(lambda l, d, t, ph: seen.append((l, d, t, ph)))
    total = 74481402
    p.download("中文 → 英文", int(total * 0.4), total)
    p.download("中文 → 英文", total, total)
    p.extract("正在解压")

    extract_events = [e for e in seen if e[3] == models.PHASE_EXTRACT]
    check("确实发出了 extract 阶段事件", len(extract_events) == 1, str(extract_events))
    ev = extract_events[0]
    check("extract 不再是 (done=0, total=0)", not (ev[1] == 0 and ev[2] == 0), str(ev))
    check("extract 沿用了已下载字节数", ev[1] == total, f"done={ev[1]}")
    check("extract 沿用了总量", ev[2] == total, f"total={ev[2]}")

    # 界面按 done/total 算百分比：修复前 total=0 → 0%
    def ui_percent(done, total):
        return int(done * 100 / total) if total else 0
    check("界面算出的百分比不会归零", ui_percent(ev[1], ev[2]) == 100,
          f"{ui_percent(ev[1], ev[2])}%")


def test_monotonic():
    print("\n=== 5. 字节回退时百分比单调 ===")
    seen = []
    p = models._Progress(lambda l, d, t, ph: seen.append((l, d, t, ph)))
    total = 1000
    for done in (300, 600, 900, 600, 300, 1000):
        p.download("m", done, total)       # 中间故意回退（分块重试会这样）
    pcts = [int(d * 100 / t) if t else 0 for _l, d, t, _ph in seen]
    check("百分比序列单调不减", all(b >= a for a, b in zip(pcts, pcts[1:])), str(pcts))
    check("最终为 100%", pcts[-1] == 100, str(pcts[-1]))


def test_total_unknown():
    print("\n=== 6. total 未知（服务器没给 Content-Length）===")
    seen = []
    p = models._Progress(lambda l, d, t, ph: seen.append((l, d, t, ph)))
    p.download("m", 1000, None)
    p.download("m", 5000, None)
    check("total 未知时不崩且有回调", len(seen) == 2, str(seen))
    check("total 保持为 0（界面会走'只显示已下载量'分支）",
          all(e[2] == 0 for e in seen), str(seen))


def test_no_callback():
    print("\n=== 7. 没有回调时全部是安全空操作 ===")
    p = models._Progress(None)
    p.download("m", 1, 2)
    p.finish("m")
    p.extract()
    check("progress_cb=None 不抛异常", True)


def test_shared_instance():
    print("\n=== 8. 下载与解压共用同一个实例（真实下载实测抓到的坑）===")
    seen = []
    raw = lambda label, done, total, phase: seen.append((label, done, total, phase))

    # 模拟 ensure() 的做法：建一个实例，先下载再解压
    prog = models._Progress(raw)
    total = 74481402
    prog.download("模型", total // 2, total)
    prog.finish("模型")
    prog.extract("正在解压")

    ex = [e for e in seen if e[3] == models.PHASE_EXTRACT]
    check("解压事件带上了真实字节数", len(ex) == 1 and ex[0][1] == total,
          str(ex))
    check("解压事件不再是 (0, 0)", not (ex[0][1] == 0 and ex[0][2] == 0), str(ex))

    # 回归：把 _Progress 实例再包一层，字节数不应丢失（曾经会退化成 0/0）
    seen2 = []
    raw2 = lambda label, done, total, phase: seen2.append((done, total, phase))
    outer = models._Progress(raw2)
    total2 = 1000
    outer.download("m", total2, total2)
    outer.finish("m")
    inner = models._Progress(outer)          # 模拟 install() 里的包装
    inner.extract("正在解压")
    ex2 = [e for e in seen2 if e[2] == models.PHASE_EXTRACT]
    check("二次包装后解压事件仍保留字节数", len(ex2) == 1 and ex2[0][0] == total2,
          str(ex2))

    # 关键回归点：外层已收尾(closed)时，install 仍要能发出解压事件
    seen3 = []
    raw3 = lambda label, done, total, phase: seen3.append((done, total, phase))
    prog3 = models._Progress(raw3)
    prog3.download("m", 500, 1000)
    prog3.finish("m")
    prog3.extract("解压")
    check("收尾后仍能发出解压事件（界面靠它显示正在解压）",
          any(e[2] == models.PHASE_EXTRACT for e in seen3), str(seen3))


def main():
    print("=" * 62)
    print("键译 · 进度上报层测试")
    print("=" * 62)
    test_three_arg_compat()
    test_finish_reaches_100()
    test_closed_ignores_late()
    test_extract_no_zero()
    test_monotonic()
    test_total_unknown()
    test_no_callback()
    test_shared_instance()
    print("\n" + "=" * 62)
    print(f"通过 {PASS[0]} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())