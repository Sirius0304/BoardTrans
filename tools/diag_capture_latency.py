# -*- coding: utf-8 -*-
"""取词启动延迟：改前 / 改后 对照测量（2026-10-16 方案①）

为什么用"假应用"而不是真窗口：
  同进程注入按键有竞态，真窗口测量无法稳定复现；而这里要量的**只是等待逻辑**
  （探测窗口 + 重试等待），用假剪贴板/假控件即可得到与真机一致的时序
  —— 真实等待（time.sleep 轮询）一分不少，被替换的只有"目标程序"。

三个场景：
  A 无选区（常态：打完中文直接按快捷键）—— 主战场
  B 有选区且复制很快（30ms）
  C 有选区但复制很慢（800ms）—— 展示短窗口的取舍，以及调大配置后的恢复

用法：.venv\\Scripts\\python.exe tools\\diag_capture_latency.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import capture   # noqa: E402


class FakeClipboard:
    """模拟剪贴板：set/get + 序列号；可安排"延迟 N 秒后才写入"的复制。"""

    def __init__(self):
        self.value = "（用户原来的剪贴板内容）"
        self.seq = 1
        self._pending = None          # (生效时刻, 文本)

    def set(self, text):
        self.value = text
        self.seq += 1

    def get(self):
        if self._pending and time.monotonic() >= self._pending[0]:
            self.value, self.seq = self._pending[1], self.seq + 1
            self._pending = None
        return self.value

    def schedule(self, text, delay):
        self._pending = (time.monotonic() + delay, text)

    def seq_no(self):
        self.get()                    # 让到期的写入先生效
        return self.seq


class FakeApp:
    """模拟带选区的编辑控件：按键改变选区；Ctrl+C 时复制"当前选区"（延迟后写剪贴板）。

    selection=None 表示用户没有选中任何东西（此时第一次 Ctrl+C 拿不到任何内容，
    与真机一致）；一旦程序自己按下 shift+home / ctrl+shift+up，当前选区就变成
    控件里的文字，Ctrl+C 自然能复制到 —— 这正是"自动取段"依赖的行为。
    """

    def __init__(self, rows, selection=None, copy_delay=0.03):
        self.rows = list(rows)
        self.o = len(self.rows) - 1       # 光标所在行
        self.c = len(self.rows[-1])       # 光标列（行尾）
        self.t = self.o                   # 选区顶部
        self.selection = selection        # None = 用户没有选中任何东西
        self.copy_delay = copy_delay
        self.touched = False              # 程序是否已经动过选区
        self.keys = []

    def send(self, key):
        self.keys.append(key)
        if key == "ctrl+c":
            # 没动过选区时：用户手动选中了才复制得到东西，只有光标则什么都复制不到；
            # 程序按键建立过选区后：复制到的就是控件里的选区文本。
            if self.touched:
                text = self.copy_text()
            else:
                text = self.selection
            if text:
                clip.schedule(text, self.copy_delay)
            return
        self.touched = True
        if key == "shift+home":
            self.t = self.o
        elif key == "shift+up":
            self.t = max(0, self.t - 1)
        elif key == "shift+down":
            self.t = min(self.o, self.t + 1)
        elif key == "ctrl+shift+up":
            self.t = 0

    def copy_text(self):
        if self.t == self.o:
            return self.rows[self.o][:self.c]
        return "\n".join(self.rows[self.t:self.o] + [self.rows[self.o][:self.c]])


clip = None          # 当前场景的假剪贴板（供 FakeApp.send 调度复制）


def _install(app):
    """把 capture 的内部依赖换成假实现；返回 restore 回调。"""
    global clip
    clip = FakeClipboard()
    saved = (capture.keyboard.send, capture.set_clipboard_text, capture.get_clipboard_text,
             capture.clipboard_seq)
    capture.keyboard.send = app.send
    capture.set_clipboard_text = lambda t: (clip.set(t), True)[1]
    capture.get_clipboard_text = clip.get
    capture.clipboard_seq = clip.seq_no

    def restore():
        (capture.keyboard.send, capture.set_clipboard_text, capture.get_clipboard_text,
         capture.clipboard_seq) = saved
    return restore


def old_capture(mode="auto", max_chars=20000):
    """完整复刻"改前"的 capture_selected_text：
    释放修饰键 → 1.2s×2 的老探测（无序列号快判）→ 取段 → 后处理。
    与改后走**同样的外围步骤**，保证两边口径一致。
    """
    sentinel = "__sentinel__"
    backup = clip.get()
    try:
        capture.release_modifiers()
        text = ""
        for attempt in range(capture.COPY_ATTEMPTS):
            if attempt:
                time.sleep(capture.COPY_RETRY_SETTLE)
            text = capture._copy_once(sentinel)
            if text:
                break
            if attempt + 1 < capture.COPY_ATTEMPTS:
                time.sleep(0.08)
        if text:
            return capture._post_process(text, "selection", max_chars, sentinel, backup)
        text, lines = capture._capture_paragraph(sentinel, max_chars)
        if not text:
            return "", "没能取到文字：当前窗口可能不支持自动取词，请先手动选中文字再按快捷键"
        src = "paragraph" if lines > 1 else "line"
        return capture._post_process(text, src, max_chars, sentinel, backup)
    finally:
        capture.set_clipboard_text(backup)


def scenario(name, rows, selection, copy_delay, probe_ms):
    """返回 (改前总耗时, 改后总耗时, 改后取到的文本, 改后来源)"""
    print(f"\n=== {name} ===")

    # 改前
    app = FakeApp(rows, selection=selection, copy_delay=copy_delay)
    restore = _install(app)
    try:
        t0 = time.monotonic()
        text, _how = old_capture()
        old_total = time.monotonic() - t0
    finally:
        restore()
    print(f"  改前：{old_total * 1000:7.0f} ms  取到 {len(text)} 字")

    # 改后（真实代码路径，含 config 传入的探测窗口）
    app = FakeApp(rows, selection=selection, copy_delay=copy_delay)
    restore = _install(app)
    try:
        t0 = time.monotonic()
        text2, how = capture.capture_selected_text("auto", 20000, probe_ms=probe_ms)
        new_total = time.monotonic() - t0
    finally:
        restore()
    print(f"  改后：{new_total * 1000:7.0f} ms  取到 {len(text2)} 字（{how}）")
    print(f"  提速：{(old_total - new_total) * 1000:.0f} ms")
    return old_total, new_total, text2, how


def main():
    rows = ["这是第一行文字，", "第二行接着写，", "第三行也在这里，", "第四行到头了。"]

    print("=" * 72)
    print("取词启动延迟对照（假应用，真实等待时序）")
    print(f"当前参数：探测窗口 {capture.SELECTION_PROBE_TIMEOUT * 1000:.0f}ms × "
          f"{capture.SELECTION_PROBE_ATTEMPTS} 次 | 旧复制超时 {capture.COPY_TIMEOUT}s × "
          f"{capture.COPY_ATTEMPTS} 次")
    print("=" * 72)

    # A 无选区（常态）
    o1, n1, t1, how1 = scenario("A 无选区（打完字直接按快捷键，最常见）",
                                rows, selection=None, copy_delay=0.03, probe_ms=150)
    assert t1 == "\n".join(rows), f"应取到整段，实际 {t1!r}"
    assert "自动选中本段" in how1, how1

    # B 有选区、复制 30ms
    o2, n2, t2, how2 = scenario("B 有选区（复制 30ms 完成）",
                                rows, selection="用户选中的一段话", copy_delay=0.03,
                                probe_ms=150)
    assert t2 == "用户选中的一段话", repr(t2)
    assert how2 == "已选中内容", how2

    # C 有选区但复制很慢（800ms）——短窗口的取舍
    o3, n3, t3, how3 = scenario("C 有选区但复制慢（800ms，极端情况）",
                                rows, selection="用户选中的一段话", copy_delay=0.8,
                                probe_ms=150)
    print("      ^ 注意：改后把这种情况判成了「未选中」→ 转而取整段。"
          "这是短窗口的代价，可用 selection_probe_ms 调大规避（见下）")
    o4, n4, t4, how4 = scenario("C' 同上，但把 selection_probe_ms 调大到 1200",
                                rows, selection="用户选中的一段话", copy_delay=0.8,
                                probe_ms=1200)
    assert t4 == "用户选中的一段话", repr(t4)

    print("\n" + "=" * 72)
    print(f"A 常态（无选区）：{o1 * 1000:.0f} ms → {n1 * 1000:.0f} ms")
    print(f"B 有选区快复制：{o2 * 1000:.0f} ms → {n2 * 1000:.0f} ms")
    print(f"C 慢复制 800ms：改后 {n3 * 1000:.0f} ms（误判为未选中，取整段）；"
          f"调大配置后 {n4 * 1000:.0f} ms 恢复正常")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
