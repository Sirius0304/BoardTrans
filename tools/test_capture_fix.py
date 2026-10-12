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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import capture, config   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


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

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
