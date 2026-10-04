# -*- coding: utf-8 -*-
r"""诊断：验证对话框"直接出现在设置界面正中央、零闪烁"（2026-10-03 修左上角闪烁）。

两层验证：
  1) **帧级**（Tk 事件循环内）：每 4ms 采一次 (alpha, 客户区原点, 尺寸)，检查
     ① 窗口创建后一直是撤隐态（mapped=0），② 露面第一帧就已居中（误差 ≤2px），
     ③ 露面之后位置与尺寸都不再变化（没有二次跳动）。
  2) **像素级**（屏幕级，后台线程抓屏）：盯屏幕左上角 100x100 区域，与弹窗打开前的
     参考图逐像素比对 —— 这是主人报障的原点（"先在左上角闪一下再跳过去"）。
     读代码猜不出来，必须看屏幕。

用法：F:\shunyi\.venv\Scripts\python.exe tools\diag_dialog_center.py
退出码 0 = 全部通过。
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.settings_window as sw                        # noqa: E402
from app import dialogs                                 # noqa: E402
from app.service import HotkeyService                   # noqa: E402

MSG = ("确定要删除「中译英」模型吗？\n将释放约 82 MB 磁盘空间。\n"
       "注意：中译英是基础模型，删除后中译英和中译日都无法翻译。")
BTNS = [{"text": "取消", "value": "cancel", "kind": "secondary"},
        {"text": "删除", "value": "ok", "kind": "danger"}]

WATCH = (0, 0, 100, 100)      # 屏幕左上角盯防区（设置窗口在 1010,329，盖不到这里）


class _TopLeftWatcher(threading.Thread):
    """后台逐帧抓屏幕左上角，与参考图逐像素比，看有没有被画过。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples = []
        self.ref = None
        self.stop_flag = False
        self.t0 = 0.0

    @staticmethod
    def _grab():
        from PIL import ImageGrab                # noqa: PLC0415
        return list(ImageGrab.grab(bbox=WATCH).convert("RGB").getdata())

    def take_ref(self):
        self.ref = self._grab()
        return self.ref

    def run(self):
        while not self.stop_flag:
            try:
                data = self._grab()
                n = -1 if self.ref is None else sum(
                    1 for a, b in zip(data, self.ref)
                    if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) > 30)
                self.samples.append((time.time() - self.t0, n))
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.004)


def main():
    service = HotkeyService(notify=lambda t, m: None)
    service.register_hotkey()
    st = sw.SettingsWindow(service)
    st.open()
    time.sleep(2.5)
    report = {}

    def run():
        root = st.root
        cx = root.winfo_rootx() + root.winfo_width() // 2
        cy = root.winfo_rooty() + root.winfo_height() // 2
        samples = []
        top, result, close = dialogs._build(root, "删除模型", MSG, BTNS, "err", "ok")
        report["hidden_at_build"] = not bool(top.winfo_ismapped())

        def sampler():
            try:
                # 用 winfo_rootx/rooty（客户区原点）而不是 x/y（框架原点）：
                # 两者差一个边框（实测横向 8px、纵向 31px），算中心必须用前者
                samples.append((1 if top.winfo_ismapped() else 0,
                                top.winfo_rootx(), top.winfo_rooty(),
                                top.winfo_width(), top.winfo_height()))
            except Exception:  # noqa: BLE001
                pass
            if top.winfo_exists():
                top.after(4, sampler)

        # 完全照 _run 的真实流程：先定尺寸位置并露面，再抓模态
        dialogs._center_on_parent(top, root)
        try:
            top.grab_set()
        except Exception:  # noqa: BLE001
            pass
        top.after(4, sampler)
        top.after(800, lambda: close("cancel"))
        top.wait_window(top)

        # ---- 分析 ----
        visible = [s for s in samples if s[0]]
        hidden = [s for s in samples if not s[0]]
        sizes = {(s[3], s[4]) for s in visible}
        print(f"    采样 {len(samples)} 次：撤隐 {len(hidden)} 帧，可见 {len(visible)} 帧")
        print(f"    可见期内出现过的尺寸集合：{sorted(sizes)}")
        ok_first = ok_stable = False
        if visible:
            _a, rx, ry, w, h = visible[0]
            vcx, vcy = rx + w // 2, ry + h // 2
            print(f"    第一帧可见：客户区原点=({rx},{ry}) 尺寸={w}x{h}")
            later = {(s[1], s[2]) for s in visible[1:]}
            ok_stable = (later <= {(rx, ry)}) and ({(s[3], s[4]) for s in visible[1:]} <= {(w, h)})
            ok_first = abs(vcx - cx) <= 3 and abs(vcy - cy) <= 3
            print(f"    客户区中心 = ({vcx}, {vcy})；父窗口中心 = ({cx}, {cy})；"
                  f"误差 = ({vcx - cx}, {vcy - cy})")
            print(f"    可见期内出现过的位置集合：{sorted(later)[:4]}"
                  f"{' …共%d个' % len(later) if later else ''}")
        report["first_centered"] = ok_first
        report["no_jump"] = ok_stable
        report["samples"] = len(samples)
        report["hidden_frames"] = len(hidden)
        return True

    st.run_in_ui(run, timeout=30)

    # ---------- 第二层：像素级盯防（屏幕左上角）----------
    w = _TopLeftWatcher()
    w.t0 = time.time()
    w.start()
    time.sleep(0.8)
    w.take_ref()                     # 弹窗还没开时的左上角作为基线
    time.sleep(0.4)
    t0 = time.time()
    th = threading.Thread(target=lambda: st.run_in_ui(
        lambda: dialogs.ask(st.root, "删除模型", MSG, ok_text="删除",
                            tone="err", danger=True), timeout=20), daemon=True)
    th.start()
    time.sleep(1.5)                  # 让弹窗开着
    st.run_in_ui(lambda: [c.destroy() for c in st.root.winfo_children()
                          if isinstance(c, __import__("tkinter").Toplevel)], timeout=8)
    th.join(timeout=5)
    time.sleep(0.4)
    w.stop_flag = True
    w.join(timeout=2)
    bad = [s for s in w.samples if s[1] > 0]
    print("\n=== 像素级：屏幕左上角盯防 ===")
    print(f"  抓了 {len(w.samples)} 帧，弹窗调用前后左上角被画出的帧数 = {len(bad)}")
    if bad:
        print(f"  被画出的时刻(ms) = {[round(s[0]*1000, 1) for s in bad[:20]]}")
    report["flash_frames"] = len(bad)

    st.shutdown()
    time.sleep(0.5)
    service.unregister_hotkey()

    print("\n=== 结论 ===")
    print(f"  创建后处于撤隐态（从未在左上角露出）："
          f"{'通过' if report.get('hidden_at_build') else '失败'}")
    print(f"  首帧可见即居中：{'通过' if report.get('first_centered') else '失败'}")
    print(f"  可见后位置/尺寸不再变：{'通过' if report.get('no_jump') else '失败'}")
    print(f"  屏幕左上角零帧被画出："
          f"{'通过' if report.get('flash_frames') == 0 else '失败'}"
          f"（实测 {report.get('flash_frames')} 帧）")
    ok = (report.get("hidden_at_build") and report.get("first_centered")
          and report.get("no_jump") and report.get("flash_frames") == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
