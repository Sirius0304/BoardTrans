# -*- coding: utf-8 -*-
"""取词修复回归（2026-10-12）

覆盖 2026-10-12 报障的两处修复：

  ① 长文本只翻译最后一段
     - 原：第一步 Ctrl+C 失败就**静默降级**为 shift+home（只取本行）
     - 修：复制超时 0.6s→1.2s 且**重试一次**；兜底升级为「取整段」
  ② TXT 文件无法翻译
     - 原：len(text) > max_chars(2000) 时**整单拒绝**，一个字的翻译都不做
     - 修：默认上限提到 20000，超限时给出可操作提示（告诉用户可调大）

说明：真正走窗口的取词无法在无头环境下稳定复现（同进程注入有竞态），
所以这里锁的是**确定性的纯逻辑**（阈值判定、文案、默认值），
窗口部分由 tools/diag_capture_scope.py 做人工/半自动验证。
"""
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import capture, config   # noqa: E402
from tools.diag_capture_latency import FakeClipboard   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


class FakeEditor:
    """模拟多行编辑控件：按键改变选区，复制返回选中文本。

    选区模型（够用即可）：光标原始行 O（列 C），选区顶部 T。
      shift+home      → T = O（从本行行首到光标）
      shift+up        → T -= 1
      ctrl+shift+up   → T = 0（仅当该控件支持；不支持时什么都不做）
      shift+down      → T += 1
    """

    def __init__(self, rows, oneshot=True):
        self.rows = list(rows)
        self.oneshot = oneshot
        self.o = len(self.rows) - 1            # 光标所在行（最后一行）
        self.c = len(self.rows[-1])            # 光标列（行尾）
        self.t = self.o                        # 选区顶部
        self.copies = 0
        self.keys = []

    def send(self, key):
        self.keys.append(key)
        if key == "shift+home":
            self.t = self.o
        elif key == "shift+up":
            self.t = max(0, self.t - 1)
        elif key == "shift+down":
            self.t = min(self.o, self.t + 1)
        elif key == "ctrl+shift+up":
            if self.oneshot:
                self.t = 0

    def copy(self):
        self.copies += 1
        if self.t == self.o:
            return self.rows[self.o][:self.c]
        parts = self.rows[self.t:self.o] + [self.rows[self.o][:self.c]]
        return "\n".join(parts)


def run_selection_probe(responses=(), timeout=None, attempts=None, seq_available=True,
                        copy_delay=0.0, selection=True):
    """使用真实复制/轮询逻辑；哨兵和目标程序的每次写入都会递增序列号。"""
    clip = FakeClipboard()
    pending = iter(responses)
    sent = []

    def send(key):
        sent.append((key, time.monotonic()))
        response = next(pending, None)
        if response is not None:
            clip.schedule(response, copy_delay)

    def write(text):
        clip.set(text)
        return True

    with patch.object(capture.keyboard, "send", side_effect=send), \
            patch.object(capture, "set_clipboard_text", side_effect=write), \
            patch.object(capture, "get_clipboard_text", side_effect=clip.get), \
            patch.object(capture, "clipboard_seq", side_effect=clip.seq_no if seq_available else lambda: 0), \
            patch.object(capture, "_wait_for_clipboard_change", wraps=capture._wait_for_clipboard_change) as wait, \
            patch.object(capture, "log") as logger:
        started = time.monotonic()
        if selection:
            text = capture._copy_selection("s", timeout=timeout, attempts=attempts)
        else:
            text = capture._copy_once("s")
        elapsed = time.monotonic() - started

    messages = [call.args[0] % call.args[1:] for call in logger.info.call_args_list]
    return text, sent, clip, wait.call_args_list, messages, elapsed


def _probe_config(payload_json: str) -> str:
    """在隔离的数据目录里写一份配置，读回来看看 max_chars 最终是多少。

    用子进程 + 临时 BOARDTRANS_DATA_DIR，避免污染真实配置。
    """
    import json as _json
    import subprocess
    import tempfile

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    py = os.path.join(root, ".venv", "Scripts", "python.exe")
    with tempfile.TemporaryDirectory() as td:
        cfg = os.path.join(td, "config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(payload_json)
        env = dict(os.environ)
        env["BOARDTRANS_DATA_DIR"] = td
        env.pop("SHUNYI_DATA_DIR", None)
        code = ("import sys; sys.path.insert(0, r'%s')\n"
                "from app import config\n"
                "print('RESULT=' + str(config.load(force=True).get('max_chars')))\n" % root)
        out = subprocess.run([py, "-c", code], capture_output=True, text=True,
                             env=env, encoding="utf-8", errors="replace").stdout
        for line in out.splitlines():
            if line.startswith("RESULT="):
                return line.split("=", 1)[1].strip()
        return f"<无输出: {out.strip()[:60]}>"


def main():
    print("=" * 66)
    print("取词修复回归")
    print("=" * 66)

    # 屏蔽真实按键：_post_process 会发 delete，测试里不该动系统
    sent_keys = []
    real_send = capture.keyboard.send
    capture.keyboard.send = lambda k: sent_keys.append(k)

    try:
        # ---- 1. 阈值：刚好等于上限应放行
        n = 100
        t = "中" * n
        text, how = capture._post_process(t, "paragraph", n, "s", "b")
        check("长度等于上限时放行", text == t and how == "自动选中本段", how)

        # ---- 2. 超限：拒绝，且提示里带可操作信息（可调大 max_chars）
        t2 = "中" * (n + 1)
        text2, how2 = capture._post_process(t2, "paragraph", n, "s", "b")
        check("超出上限时不翻译", text2 == "", repr(text2))
        check("超限提示指出具体字数与上限",
              str(n + 1) in how2 and str(n) in how2, how2)
        check("超限提示告诉用户怎么调大", "max_chars" in how2, how2)

        # ---- 3. 取词来源标签（selection / paragraph / line 三种）
        for src, want in (("selection", "已选中内容"),
                          ("paragraph", "自动选中本段"),
                          ("line", "自动选中本行")):
            _, label = capture._post_process("测试", src, 100, "s", "b")
            check(f"来源 {src} 的说明文案", label == want, label)

        # ---- 4. 空内容
        _, empty_how = capture._post_process("   ", "line", 100, "s", "b")
        check("空白内容被判定为空", empty_how == "取到的内容是空的", empty_how)

        # ---- 5. 默认上限已提高（TXT 文件的核心修复点）
        default_max = config.DEFAULTS.get("max_chars")
        check("max_chars 默认值已提到 20000", default_max == 20000, str(default_max))
        check("新默认值足以容纳一个普通 TXT", int(default_max) >= 20000)

        # ---- 6. 超时常量与重试次数（长文本不再被误判为"没选中"）
        check("复制超时已放宽到 1.2s（原 0.6s）", capture.COPY_TIMEOUT >= 1.2,
              str(capture.COPY_TIMEOUT))
        check("支持复制重试", capture.COPY_ATTEMPTS >= 2, str(capture.COPY_ATTEMPTS))

        # ---- 7. 整段取词有行数上限，避免无限扩
        check("整段取词有行数上限", getattr(capture, "PARAGRAPH_MAX_LINES", 0) >= 1,
              str(getattr(capture, "PARAGRAPH_MAX_LINES", None)))

        # ---- 8. capture_mode 的取值仍在支持范围内
        check("capture_mode 默认 auto", config.DEFAULTS.get("capture_mode") == "auto",
              str(config.DEFAULTS.get("capture_mode")))

        # ---- 9. line 模式仍然保留（只翻这一句的老行为）
        import inspect
        src_code = inspect.getsource(capture.capture_selected_text)
        check("line 模式仍走「只取本行」分支", 'mode == "line"' in src_code)
        check("auto 模式已改为取整段", "_capture_paragraph" in src_code)
    finally:
        capture.keyboard.send = real_send

    # ---- 10. 默认值迁移：老配置里存的"旧默认值"必须被平滑升级
    # （否则 _merge 的"磁盘值优先"会让新默认值永远不生效，修复等于白做）
    check("提供了旧默认值常量", config.OLD_MAX_CHARS_DEFAULT == 2000,
          str(config.OLD_MAX_CHARS_DEFAULT))
    r_old = _probe_config('{"max_chars": 2000}')
    check("老配置里的 2000（=旧默认）被升级为 20000", r_old == "20000", r_old)
    r_custom = _probe_config('{"max_chars": 5000}')
    check("用户自己改过的 5000 不被覆盖", r_custom == "5000", r_custom)

    # ---- 11. 一键扩段（2026-10-16）：主路径只复制 2 次；不支持该组合键时退回逐行
    print("  -- 一键扩段（假编辑器模拟）--")

    def run_fake(rows, oneshot, max_chars=2000):
        ed = FakeEditor(rows, oneshot=oneshot)
        real_send, real_copy = capture.keyboard.send, capture._copy_once
        capture.keyboard.send = ed.send
        capture._copy_once = lambda sentinel, timeout=None: ed.copy()
        try:
            return capture._capture_paragraph("s", max_chars) + (ed.copies, ed)
        finally:
            capture.keyboard.send, capture._copy_once = real_send, real_copy

    rows5 = ["第一行文字", "第二行文字", "第三行文字", "第四行文字", "第五行文字"]
    want5 = "\n".join(rows5)

    text, lines, copies, _ = run_fake(rows5, oneshot=True)
    check("一键扩段：一次拿到整段", text == want5, repr(text[:20]))
    check("一键扩段：行数正确", lines == 5, str(lines))
    check("一键扩段：只复制 2 次（旧实现要 6 次）", copies == 2, str(copies))

    text_f, lines_f, copies_f, _ = run_fake(rows5, oneshot=False)
    check("不支持 Ctrl+Shift+Up 时退回逐行，结果仍完整", text_f == want5, repr(text_f[:20]))
    check("退回逐行时行数正确", lines_f == 5, str(lines_f))
    check("退回逐行确实多复制了几次（说明走了兜底）", copies_f > 2, str(copies_f))

    rows_b = ["", "上一段第一行", "上一段第二行"]
    text_b, lines_b, copies_b, _ = run_fake(rows_b, oneshot=True)
    check("一键扩段越过段界（结果以空行开头）时保留本行结果",
          text_b == rows_b[-1] and lines_b == 1, f"{text_b!r} lines={lines_b}")

    check("扩段组合键是 ctrl+shift+up",
          capture.PARAGRAPH_EXTEND_KEY == "ctrl+shift+up", capture.PARAGRAPH_EXTEND_KEY)

    # ---- 12. 选区快速探测（2026-10-16 方案①：按键后不再白等 2.7s）
    print("  -- 选区快速探测 --")
    check("探测窗口常量为 150ms", abs(capture.SELECTION_PROBE_TIMEOUT - 0.15) < 1e-9,
          str(capture.SELECTION_PROBE_TIMEOUT))
    check("默认只探测 1 次", capture.SELECTION_PROBE_ATTEMPTS == 1,
          str(capture.SELECTION_PROBE_ATTEMPTS))
    check("轮询间隔收紧到 5ms（判定粒度更细）", capture.POLL_INTERVAL <= 0.005,
          str(capture.POLL_INTERVAL))
    check("config 提供 selection_probe_ms（默认 150）",
          config.DEFAULTS.get("selection_probe_ms") == 150,
          str(config.DEFAULTS.get("selection_probe_ms")))
    check("有剪贴板序列号判定（零成本快判）", callable(getattr(capture, "clipboard_seq", None)))

    got, sent, clip, waits, logs, fast_dt = run_selection_probe()
    check("无选区时快速探测返回空", got == "", repr(got))
    check("快速探测只复制 1 次（旧实现 2 次）", len(sent) == 1, str(sent))
    check("写哨兵确实递增序列号（不能用恒定序列号桩）", clip.seq == 2, str(clip.seq))
    check("快速探测用的是 150ms 窗口",
          len(waits) == 1 and waits[0].args[1] == capture.SELECTION_PROBE_TIMEOUT, str(waits))
    check("快速探测耗时 < 0.5s（旧实现约 2.7s）", fast_dt < 0.5, f"{fast_dt * 1000:.0f}ms")

    got, sent, clip, waits, logs, slow_dt = run_selection_probe(timeout=0.6)
    check("保守档无外部写入时也只探测一次", got == "" and len(sent) == 1, str(sent))
    check("保守档快判排除哨兵自身的序列号增量", clip.seq == 2, str(clip.seq))
    check("无外部写入时日志确实报告序列号未变",
          any("剪贴板序列号未变" in line for line in logs), str(logs))

    got, sent, clip, waits, logs, elapsed = run_selection_probe(["用户选中的内容"], copy_delay=0.03)
    check("快速复制的真实选区仍能命中", got == "用户选中的内容" and len(sent) == 1, repr(got))
    check("选区复制与写哨兵分别记账", clip.seq == 3, str(clip.seq))

    got, sent, clip, waits, logs, elapsed = run_selection_probe(["", "第二次复制成功"], timeout=0.6)
    check("序列号有变化但无文本时保留第二次探测", got == "第二次复制成功" and len(sent) == 2,
          f"{got!r} copies={len(sent)}")
    check("每次重试重新以本次哨兵写入后为基准", clip.seq == 5, str(clip.seq))
    check("两次探测之间仍等待修饰键物理抬起",
          len(sent) == 2 and sent[1][1] - sent[0][1] >= 0.6 + capture.COPY_RETRY_SETTLE,
          f"{elapsed * 1000:.0f}ms")

    got, sent, clip, waits, logs, elapsed = run_selection_probe(["", ""], timeout=0.01, attempts=2)
    check("两次外部空写入后探测正常返回空", got == "" and len(sent) == 2, str(sent))
    check("序列号变化时日志不再谎报未变",
          any("剪贴板序列号已变化" in line for line in logs) and
          not any("剪贴板序列号未变" in line for line in logs), str(logs))

    got, sent, clip, waits, logs, elapsed = run_selection_probe(timeout=0.6, seq_available=False)
    check("序列号接口返回 0 时保守档回退两次超时探测", got == "" and len(sent) == 2, str(sent))
    check("序列号不可用时日志不再谎报未变",
          any("剪贴板序列号不可用" in line for line in logs) and
          not any("剪贴板序列号未变" in line for line in logs), str(logs))

    got, sent, clip, waits, logs, elapsed = run_selection_probe(["整段复制内容"], selection=False)
    check("普通复制 _copy_once 继续返回文本而非元组", got == "整段复制内容", repr(got))

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
