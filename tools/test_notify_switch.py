# -*- coding: utf-8 -*-
"""「通知提醒」开关回归测试（针对"关掉开关仍然弹通知"缺陷）。

覆盖清单：
  A. 开关开 → 通知发出，且只发一次
  B. 开关关 → 通知被拦下（不发出）
  C. 持久化 + 重启：写盘后用**真实子进程**读配置，确认仍是关闭状态
  D. 反复切换 开→关→开→关，每次行为都与当前状态一致
  E. 两个模型（zh_en / en_ja）的下载完成提醒行为一致，且不再弹模态窗口
  F. 托盘菜单入口（TrayApp.notify）同样受开关控制
  G. 其它通知源（翻译出错、切换语言等）同样受开关控制
  H. 结构性检查：app/ 内不存在绕过统一入口的裸通知调用
  I. 同一次事件只提醒一次（去重）
  J. 配置 ↔ 设置窗口控件一致（含窗口自动跟随）
  K. 下载失败：关提醒时不弹，但卡片常驻显示失败原因 + 重试按钮

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_notify_switch.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config, notify as notify_mod, paths      # noqa: E402
import app.settings_window as sw                         # noqa: E402
from app.service import HotkeyService                     # noqa: E402
from app.tray import TrayApp                              # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


CAPTURED = []


def fake_sink(title, message):
    CAPTURED.append((title, message))


class AutoAnswer:
    """自动应答对话框，并记录弹过哪些模态窗口。"""
    answer = True
    calls: list = []

    @classmethod
    def askokcancel(cls, *a, **k):
        return cls.answer

    @classmethod
    def showinfo(cls, *a, **k):
        cls.calls.append(("info", a[0] if a else ""))

    @classmethod
    def showerror(cls, *a, **k):
        cls.calls.append(("error", a[0] if a else ""))

    @classmethod
    def showwarning(cls, *a, **k):
        cls.calls.append(("warning", a[0] if a else ""))


def main():
    sw.messagebox = AutoAnswer
    # 界面弹窗已统一走 app.dialogs（不再用系统 messagebox）：这里打桩自动应答
    sw.dialogs.ask = lambda *a, **k: True
    sw.dialogs.tell = lambda *a, **k: None
    original = dict(config.load())
    notify_mod.set_sink(fake_sink)

    print("\n=== A/B. 基础开关行为 ===")
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    CAPTURED.clear()
    sent = notify_mod.notify("测试事件", "内容")
    check("开关开：通知发出且只发一次", sent is True and len(CAPTURED) == 1, str(CAPTURED))
    config.update(notify_enabled=False)
    notify_mod.reset_dedup()
    CAPTURED.clear()
    sent = notify_mod.notify("测试事件", "内容")
    check("开关关：通知被拦下", sent is False and not CAPTURED, str(CAPTURED))

    print("\n=== C. 持久化 + 重启（真实子进程读盘）===")
    config.update(notify_enabled=False)
    with open(paths.config_path(), encoding="utf-8") as f:
        raw = json.load(f)
    check("配置文件中已写入 notify_enabled=False", raw.get("notify_enabled") is False,
          str(raw.get("notify_enabled")))
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r);\n"
         "from app import notify, paths;\n"
         "print(notify.is_enabled())" % PROJECT],
        capture_output=True, text=True)
    got = (probe.stdout or "").strip().splitlines()[-1] if probe.stdout.strip() else ""
    check("新进程（等价于重启软件）读到的仍是关闭", got == "False",
          f"stdout={probe.stdout.strip()!r} stderr={probe.stderr.strip()[-160:]!r}")

    print("\n=== D. 反复切换 开→关→开→关 ===")
    seq = [True, False, True, False]
    oks = []
    for i, want in enumerate(seq):
        config.update(notify_enabled=want)
        notify_mod.reset_dedup()
        CAPTURED.clear()
        notify_mod.notify(f"切换测试 {i}", "内容")
        oks.append(bool(CAPTURED) == want)
    check("每次切换后行为都与当前状态一致（开→关→开→关）", all(oks), str(oks))

    print("\n=== G2. 结构性检查：没有绕过统一入口的通知 ===")
    offenders = []
    app_dir = os.path.join(PROJECT, "app")
    for fn in sorted(os.listdir(app_dir)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(app_dir, fn)
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                code = line.split("#")[0]
                if "icon.notify(" in code and fn != "tray.py":
                    offenders.append(f"{fn}:{i} 裸气泡调用")
                if re.search(r"service\._notify\s*=", code):
                    offenders.append(f"{fn}:{i} 外部替换服务通知函数")
    check("app/ 内无裸通知调用 / 无外部替换 service._notify", not offenders, str(offenders))

    print("\n=== F. 托盘入口受开关控制 ===")
    service = HotkeyService()
    service.preload = lambda: None          # 测试不加载模型
    tray = TrayApp(service)                 # 只构造，不进入消息循环
    check("托盘构造时把气泡注册为统一入口的 sink",
          getattr(notify_mod, "_sink", None) == tray._balloon)
    check("服务的通知函数就是统一入口", service._notify is notify_mod.notify)

    balloon = []
    tray._balloon = lambda t, m: balloon.append((t, m))
    notify_mod.set_sink(tray._balloon)
    config.update(notify_enabled=False)
    notify_mod.reset_dedup()
    tray.notify("用法：打字后按 alt+j", "译文会替换你刚输入的内容。")
    check("托盘菜单提醒：开关关 → 不弹", balloon == [], str(balloon))
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    tray.notify("用法：打字后按 alt+j", "译文会替换你刚输入的内容。")
    check("托盘菜单提醒：开关开 → 弹一次", len(balloon) == 1, str(balloon))

    print("\n=== G. 其它通知源受开关控制 ===")
    notify_mod.set_sink(fake_sink)
    config.update(notify_enabled=False)
    notify_mod.reset_dedup()
    CAPTURED.clear()
    service._notify("翻译出错", "详情见日志文件")
    service.notify("目标语言已切换", "英文")
    service._notify("快捷键注册失败", "「alt+j」无法注册")
    check("开关关：翻译出错 / 切换语言 / 快捷键冲突 全部不弹", not CAPTURED, str(CAPTURED))
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    CAPTURED.clear()
    service._notify("翻译出错", "详情见日志文件")
    check("开关开：这些提醒恢复正常", len(CAPTURED) == 1, str(CAPTURED))

    print("\n=== H. 同一次事件只提醒一次（去重）===")
    notify_mod.reset_dedup()
    CAPTURED.clear()
    notify_mod.notify("重复测试", "同一内容")
    notify_mod.notify("重复测试", "同一内容")
    notify_mod.notify("重复测试", "同一内容")
    check("3 秒内同一事件只发一次", len(CAPTURED) == 1, str(CAPTURED))
    notify_mod.reset_dedup()
    CAPTURED.clear()
    notify_mod.notify("重复测试", "同一内容")
    check("去重窗口过后可再次提醒", len(CAPTURED) == 1, str(CAPTURED))

    print("\n=== 打开设置窗口 ===")
    settings = sw.SettingsWindow(service)
    settings.open()
    time.sleep(2.5)
    if settings.root is None:
        check("设置窗口创建成功", False, "窗口未创建，后续用例无法执行")
        return 1
    check("设置窗口创建成功", True)

    holder = {}

    def grab_var():
        holder["var"] = bool(settings._notify_enabled_var.get())

    print("\n=== I. 配置 ↔ 窗口控件一致 ===")
    # 注意先后顺序：先等窗口自己同步（_pump 每 120ms 一次），再读取控件，
    # 否则读到的还是同步前的旧值（这是测试脚本的时序问题，不是产品问题）
    config.update(notify_enabled=False)
    time.sleep(1.2)
    settings._queue.put(("call", grab_var))
    time.sleep(0.6)
    check("配置为关 → 窗口控件未勾选", holder.get("var") is False, str(holder.get("var")))
    config.update(notify_enabled=True)          # 模拟托盘/手改配置
    time.sleep(1.2)
    settings._queue.put(("call", grab_var))
    time.sleep(0.6)
    check("配置改回开 → 窗口自动同步为勾选", holder.get("var") is True, str(holder.get("var")))

    print("\n=== E. 两个模型的下载完成提醒（走真实回调）===")
    for key in ("zh_en", "en_ja"):
        for enabled in (True, False):
            config.update(notify_enabled=enabled)
            notify_mod.reset_dedup()
            CAPTURED.clear()
            AutoAnswer.calls.clear()
            settings._queue.put(("call", lambda k=key: settings._on_download_done(k, True, "")))
            time.sleep(0.9)
            expect = 1 if enabled else 0
            tag = "开" if enabled else "关"
            check(f"{key} 下载完成提醒：开关{tag} → {expect} 条", len(CAPTURED) == expect,
                  f"实际={CAPTURED}")
            if enabled:
                check(f"{key}：不再弹「下载完成」模态窗口",
                      not any("下载完成" in str(c[1]) for c in AutoAnswer.calls),
                      str(AutoAnswer.calls))

    print("\n=== K. 下载失败：关提醒不弹，但卡片要有原因 ===")
    config.update(notify_enabled=False)
    notify_mod.reset_dedup()
    CAPTURED.clear()

    def fail_once():
        settings._on_download_done("zh_en", False, "分块下载失败：TimeoutError: timed out")

    settings._queue.put(("call", fail_once))
    time.sleep(1.0)
    check("下载失败：开关关 → 不弹提醒", not CAPTURED, str(CAPTURED))

    def grab_card():
        holder["status"] = settings._model_ui["zh_en"]["status"].cget("text")
        holder["action"] = settings._model_ui["zh_en"]["action"].cget("text")

    settings._queue.put(("call", grab_card))
    time.sleep(0.8)
    check("下载失败：卡片常驻显示失败原因", "下载失败" in str(holder.get("status")),
          str(holder.get("status")))
    check("下载失败：按钮变为「重试下载」", holder.get("action") == "重试下载",
          str(holder.get("action")))

    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    CAPTURED.clear()
    settings._queue.put(("call", fail_once))
    time.sleep(1.0)
    check("下载失败：开关开 → 弹一次", len(CAPTURED) == 1, str(CAPTURED))

    print("\n=== L. 取消下载不算失败（不弹提醒、卡片回到未下载）===")
    notify_mod.reset_dedup()
    CAPTURED.clear()

    def cancel_once():
        settings._on_download_done("zh_en", False, "已取消下载")

    settings._queue.put(("call", cancel_once))
    time.sleep(1.0)
    settings._queue.put(("call", grab_card))
    time.sleep(0.6)
    check("取消：不弹提醒", not CAPTURED, str(CAPTURED))
    # 取消不是失败：卡片不能显示"下载失败"（本机 zh_en 已安装，因此会显示"已下载"）
    check("取消：卡片不显示「下载失败」",
          "下载失败" not in str(holder.get("status")) and "取消" not in str(holder.get("status")),
          str(holder.get("status")))

    print("\n=== M. 回归：下载完成回调不得吞掉未保存的改动（本次缺陷根因）===")
    # 场景复刻：用户取消勾选开关（或改了档位）后，没点保存就去下载模型，
    # 下载完成 → 旧代码会把 _dirty 清掉并让界面同步回旧值，改动凭空消失
    def edit_then_download():
        settings._quality_var.set("quality")
        settings._pending_quality = "quality"
        settings._mark_dirty()
        holder["dirty_before"] = settings._dirty
        settings._on_download_done("zh_en", True, "")
        holder["dirty_after"] = settings._dirty
        holder["quality_after"] = settings._quality_var.get()

    config.update(notify_enabled=True)
    settings._queue.put(("call", edit_then_download))
    time.sleep(1.6)                      # 留出至少几轮 120ms 的自动同步
    check("改动前处于「未保存」状态", holder.get("dirty_before") is True, str(holder.get("dirty_before")))
    check("下载完成后仍是「未保存」（没被静默清掉）", holder.get("dirty_after") is True,
          str(holder.get("dirty_after")))
    check("下载完成后未被静默回滚（档位仍是用户改的值）", holder.get("quality_after") == "quality",
          str(holder.get("quality_after")))
    # 收尾：把这次未保存的改动丢掉，避免影响后续用例
    settings._queue.put(("call", lambda: settings._load_from_config(force=True)))
    time.sleep(0.8)

    print("\n=== N. 点一下开关就生效（不需要点保存），且不会被静默回滚 ===")
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    CAPTURED.clear()

    def click_switch_off():
        settings._notify_enabled_var.set(False)
        settings._on_notify_change()

    settings._queue.put(("call", click_switch_off))
    time.sleep(1.5)                      # 等若干轮自动同步
    check("点一下立即写盘生效", config.load().get("notify_enabled") is False,
          str(config.load().get("notify_enabled")))
    check("立即生效：随后通知被拦下", notify_mod.notify("点一下测试", "内容") is False and not CAPTURED,
          str(CAPTURED))
    settings._queue.put(("call", grab_var))
    time.sleep(0.6)
    check("不会被静默回滚（勾选框仍为未勾选）", holder.get("var") is False, str(holder.get("var")))

    def click_switch_on():
        settings._notify_enabled_var.set(True)
        settings._on_notify_change()

    settings._queue.put(("call", click_switch_on))
    time.sleep(1.2)
    CAPTURED.clear()
    check("再点一下立即恢复提醒",
          notify_mod.notify("点一下测试2", "内容") is True and len(CAPTURED) == 1, str(CAPTURED))

    print("\n=== O. 多实例/手改配置：别的进程改了要立刻生效 ===")
    # O1: 模拟"另一个实例"（真实子进程）把开关关掉
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    CAPTURED.clear()
    CODE = ("import sys; sys.path.insert(0, sys.argv[1]);"
            "from app import config; config.update(notify_enabled=False)")
    probe = subprocess.run([sys.executable, "-c", CODE, PROJECT], capture_output=True, text=True)
    check("另一个进程写配置成功", probe.returncode == 0, probe.stderr.strip()[-120:])
    check("本进程立刻跟随（不再用启动时的旧值）", notify_mod.is_enabled() is False,
          str(notify_mod.is_enabled()))
    check("本进程的通知确实被拦下",
          notify_mod.notify("多实例测试", "内容") is False and not CAPTURED, str(CAPTURED))

    # O2: 模拟"用户用记事本手工编辑 config.json"
    path = paths.config_path()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["notify_enabled"] = True
    with open(path, "w", encoding="utf-8") as f:      # 直接覆盖写，模拟手工编辑
        json.dump(raw, f, ensure_ascii=False, indent=2)
    check("手工编辑后本进程立刻读到新值", notify_mod.is_enabled() is True,
          str(notify_mod.is_enabled()))
    config.update(**{k: original[k] for k in original})   # 还原
    notify_mod.reset_dedup()
    CAPTURED.clear()

    print("\n=== P. 首次安装（配置文件不存在，独立数据目录）===")
    import tempfile
    fresh = tempfile.mkdtemp(prefix="shunyi_fresh_")
    env = dict(os.environ)
    env["SHUNYI_DATA_DIR"] = fresh
    CODE2 = ("import os, sys; sys.path.insert(0, sys.argv[1]);"
             "from app import config, notify, paths;"
             "print('enabled', notify.is_enabled());"
             "print('created', os.path.exists(paths.config_path()));"
             "config.update(notify_enabled=False);"
             "print('after_off', notify.is_enabled())")
    probe2 = subprocess.run([sys.executable, "-c", CODE2, PROJECT],
                            capture_output=True, text=True, env=env)
    out2 = probe2.stdout
    check("首次安装默认开启提醒（默认值 True）", "enabled True" in out2, out2.strip()[-160:])
    check("全新数据目录：首次读取即自动创建默认配置", "created True" in out2, out2.strip()[-160:])
    check("首次安装后关掉开关同样立即生效", "after_off False" in out2, out2.strip()[-160:])
    shutil.rmtree(fresh, ignore_errors=True)

    print("\n=== Q. 无托盘场景（控制台模式）：仍受开关控制 ===")
    import io
    from contextlib import redirect_stdout

    notify_mod.set_sink(None)                    # 等价于阶段一控制台版：没有托盘
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    buf = io.StringIO()
    with redirect_stdout(buf):
        ok_on = notify_mod.notify("控制台模式", "应当打印出来")
    check("无托盘 + 开关开：回退为控制台输出（不弹任何 UI）",
          ok_on is True and "控制台模式" in buf.getvalue(), repr(buf.getvalue()[:80]))

    config.update(notify_enabled=False)
    notify_mod.reset_dedup()
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        ok_off = notify_mod.notify("控制台模式", "不应输出")
    check("无托盘 + 开关关：连控制台输出也没有",
          ok_off is False and buf2.getvalue() == "", repr(buf2.getvalue()))
    notify_mod.set_sink(fake_sink)

    print("\n=== R. 下载途中切换开关：以通知时刻的开关为准 ===")
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    CAPTURED.clear()

    def mid_download_off():
        settings._on_download_progress("zh_en", 1, 100)      # 下载进行中
        settings._notify_enabled_var.set(False)
        settings._on_notify_change()                         # 途中关掉
        settings._on_download_done("zh_en", True, "")        # 完成

    settings._queue.put(("call", mid_download_off))
    time.sleep(1.4)
    check("下载途中关掉开关 → 完成时不弹", not CAPTURED, str(CAPTURED))

    config.update(notify_enabled=False)
    notify_mod.reset_dedup()
    CAPTURED.clear()

    def mid_download_on():
        settings._notify_enabled_var.set(True)
        settings._on_notify_change()                         # 途中打开
        settings._on_download_done("en_ja", True, "")

    settings._queue.put(("call", mid_download_on))
    time.sleep(1.4)
    check("下载途中打开开关 → 完成时弹一次", len(CAPTURED) == 1, str(CAPTURED))

    print("\n=== S. 依赖模型一并下载：只提醒一次，且不重复 ===")
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    CAPTURED.clear()

    def dep_flow():
        settings._on_dep_start("en_ja", "zh_en")             # 依赖模型开始下载（只改文字）
        settings._on_download_done("en_ja", True, "")        # 整批完成只推一次

    settings._queue.put(("call", dep_flow))
    time.sleep(1.4)
    check("依赖流程只产生 1 条提醒", len(CAPTURED) == 1, str(CAPTURED))

    print("\n=== T. 复刻用户报障流程：点关开关 → 不点保存 → 下载完成 ===")
    config.update(notify_enabled=True)
    notify_mod.reset_dedup()
    CAPTURED.clear()
    holder.clear()

    def user_flow():
        settings._notify_enabled_var.set(False)
        settings._on_notify_change()          # 等价于用户点了一下勾选框（没点「保存」）
        holder["immediate"] = notify_mod.is_enabled()
        settings._on_download_done("zh_en", True, "")   # 下载完成（用户没保存就去下载了）

    settings._queue.put(("call", user_flow))
    time.sleep(2.0)                            # 跨过若干轮自动同步，检验不会被回滚
    settings._queue.put(("call", grab_var))
    time.sleep(0.6)
    check("点关后立即生效（配置已落盘 false）", holder.get("immediate") is False,
          str(holder.get("immediate")))
    check("下载完成时零提醒", not CAPTURED, str(CAPTURED))
    check("勾选框未被静默改回（仍为未勾选）", holder.get("var") is False, str(holder.get("var")))
    check("配置文件里也是 false（重启后仍关闭）", config.load().get("notify_enabled") is False,
          str(config.load().get("notify_enabled")))

    # 复位
    config.update(notify_enabled=True)
    settings.shutdown()
    config.update(**{k: original[k] for k in original})
    time.sleep(0.5)

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
