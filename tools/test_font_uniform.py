# -*- coding: utf-8 -*-
"""设置界面字体**统一且锐利**：回归测试（递归遍历控件树，逐项核对实际字体）。

判定标准（不再写死某个族名）：
  1) 全界面所有文字元素必须用**同一个族名**（"统一"这个不变量）；
  2) 该族名必须是锐利候选（雅黑系 / 黑体系），不能退化成 TkDefaultFont 等兜底；
  3) 该族名与 settings_window.FONT_FAMILY 一致（当前默认：微软雅黑，ClearType 渲染）。

重要（踩过的坑）：设置窗口的 Tk 跑在**独立线程**，tkinter 不是线程安全的 ——
在其它线程里调 `widget.cget` / `style.lookup` / `font actual` 会得到不可靠结果
（甚至抛 "Calling Tcl from different apartment"）。所以本测试把**所有** Tk 查询
都通过 `settings._queue.put(("call", ...))` 丢进窗口线程执行，再回到主线程断言。

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_font_simhei.py
"""
import os
import sys
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config                        # noqa: E402
import app.settings_window as sw              # noqa: E402
from app.service import HotkeyService         # noqa: E402

# 锐利候选（按实测"中间灰阶占比"排序）：雅黑系走 ClearType，黑体系灰度抗锯齿偏软
CRISP_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "黑体", "SimHei")
RESULTS = []

NAMED_CUSTOM = ("ShunYiBase", "ShunYiBold", "ShunYiTitle", "ShunYiSmall", "ShunYiMono")
NAMED_TK = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont",
            "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkFixedFont")
TEXT_CLASSES = {
    "Label", "Button", "Entry", "Text", "Listbox", "Checkbutton", "Radiobutton", "Message",
    "TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TCombobox", "TEntry", "TMenubutton",
    "TLabelframe",
}


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


class AutoAnswer:
    @staticmethod
    def askokcancel(*a, **k):
        return True

    @staticmethod
    def showinfo(*a, **k):
        return None

    @staticmethod
    def showerror(*a, **k):
        return None

    @staticmethod
    def showwarning(*a, **k):
        return None


def collect(root):
    """在**窗口线程**里采集字体信息（返回普通 dict/list，供主线程断言）。"""
    style = sw.ttk.Style(root)

    def fam_of(spec):
        if not spec:
            return None
        try:
            return str(root.tk.call("font", "actual", spec, "-family"))
        except Exception:  # noqa: BLE001
            return None

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    def text_of(w):
        for opt in ("text", "value"):
            try:
                val = w.cget(opt)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            except Exception:  # noqa: BLE001
                pass
        if w.winfo_class() in ("Entry", "TEntry"):
            try:
                return str(w.get()).strip()
            except Exception:  # noqa: BLE001
                return ""
        return ""

    rows = []
    for w in walk(root):
        cls = w.winfo_class()
        if cls == "Canvas":
            # 自绘控件（圆角按钮 / 分段选项 / 胶囊 / 图标）的文字是 Canvas 文本项，
            # 也必须逐项核对字体，否则"按钮文字"这一块就成了验证盲区。
            try:
                items = w.find_all()
            except Exception:  # noqa: BLE001
                items = []
            for item in items:
                try:
                    if w.type(item) != "text":
                        continue
                    txt = str(w.itemcget(item, "text") or "").strip()
                    if not txt:
                        continue
                    spec = w.itemcget(item, "font")
                except Exception:  # noqa: BLE001
                    continue
                rows.append({"class": "Canvas", "text": txt,
                             "family": fam_of(spec) or fam_of("TkDefaultFont")})
            continue
        if cls not in TEXT_CLASSES:
            continue
        text = text_of(w)
        family = None
        if cls.startswith("T"):
            stylename = None
            try:
                stylename = w.cget("style") or None
            except Exception:  # noqa: BLE001
                stylename = None
            for cand in ([stylename] if stylename else []) + [cls, "."]:
                got = None
                try:
                    got = style.lookup(cand, "font")
                except Exception:  # noqa: BLE001
                    got = None
                family = fam_of(got) if got else None
                if family:
                    break
            if not family:
                family = fam_of("TkDefaultFont")
        else:
            spec = None
            try:
                spec = w.cget("font")
            except Exception:  # noqa: BLE001
                spec = None
            family = fam_of(spec) or fam_of("TkDefaultFont")
        rows.append({"class": cls, "text": text, "family": family})

    return {
        "theme": style.theme_use(),
        "custom": {n: fam_of(n) for n in NAMED_CUSTOM},
        "tk_named": {n: fam_of(n) for n in NAMED_TK},
        "rows": rows,
        "raw_button_font": style.lookup("TButton", "font"),
        "button_family": fam_of(style.lookup("TButton", "font")),
        "raw_combo_font": style.lookup("TCombobox", "font"),
        "combo_family": fam_of(style.lookup("TCombobox", "font")),
        "checkbutton_family": fam_of(style.lookup("TCheckbutton", "font")),
    }


def main():
    sw.messagebox = AutoAnswer
    original = dict(config.load())
    service = HotkeyService(notify=lambda t, m: None)
    service.register_hotkey()
    settings = sw.SettingsWindow(service)
    service.settings_launcher = settings.open

    settings.open()
    time.sleep(2.5)
    if settings.root is None:
        check("设置窗口创建成功", False, "窗口未创建")
        return 1
    check("设置窗口创建成功", True)

    holder = {}

    def _collect():
        data = collect(settings.root)
        data["runtime_family"] = str(
            settings.root.tk.call("font", "configure", "ShunYiBase", "-family"))
        holder.update(data)

    settings._queue.put(("call", _collect))
    time.sleep(1.0)

    data = holder
    rows = data.get("rows", [])
    TARGET = data.get("runtime_family") or ""
    check("界面字体为锐利候选（非兜底字体）", TARGET in CRISP_CANDIDATES, f"实际={TARGET}")
    check("界面字体与设定的默认族一致（FONT_FAMILY）", TARGET == sw.FONT_FAMILY,
          f"实际={TARGET} 期望={sw.FONT_FAMILY}")
    print(f"      ttk 主题: {data.get('theme')}；TButton 样式字体: {data.get('raw_button_font')!r}")

    print("\n=== A. 命名字体（自定义 + Tk 内置）===")
    bad = {k: v for k, v in (data.get("custom") or {}).items() if v != TARGET}
    check("5 个界面命名字体全部为黑体" if TARGET == "黑体" else f"5 个界面命名字体全部为 {TARGET}",
          not bad, str(bad))
    bad2 = {k: v for k, v in (data.get("tk_named") or {}).items() if v != TARGET}
    check(f"9 个 Tk 内置命名字体全部为 {TARGET}（未指定字体的控件随之统一）",
          not bad2, str(bad2))

    print("\n=== B. 控件树逐项核对（所有可见文字）===")
    print(f"      共核对 {len(rows)} 个文字控件：")
    for r in rows:
        print(f"        {r['class']:<14} {str(r['family']):<6} {r['text'][:34]}")

    bad_rows = [f"{r['class']}({r['text'][:12]})={r['family']}" for r in rows if r["family"] != TARGET]
    check(f"全部 {len(rows)} 个文字控件的实际字体都是 {TARGET}", not bad_rows, str(bad_rows[:8]))

    print("\n=== C. 关键文字元素都在核对范围内（防漏项）===")
    texts = [r["text"] for r in rows]
    must = ["瞬译 设置", "翻译模型", "中译英", "中译日", "目标语言", "英文", "日文",
            "翻译档位", "高速度", "高质量", "内存优化", "通知提醒", "快捷键",
            "保存", "取消", "恢复默认设置", "下载", "删除", "录制", "恢复默认"]
    missing = [m for m in must if not any(m in t for t in texts)]
    check("标题/标签/按钮/选项/提示等关键文字均被覆盖", not missing, f"缺少 {missing}")
    entry_rows = [r for r in rows if r["class"] in ("Entry", "TEntry") and r["text"]]
    check("输入框内的文字（快捷键值）也被覆盖", bool(entry_rows), str(entry_rows))

    print("\n=== D. 样式级字体（按钮/单选/复选/下拉）===")
    check("TButton 样式字体与界面统一（命名族名解析后）", data.get("button_family") == TARGET,
          f"{data.get('raw_button_font')!r} → {data.get('button_family')}")
    check("TCheckbutton 样式字体与界面统一", data.get("checkbutton_family") == TARGET,
          str(data.get("checkbutton_family")))
    check("TCombobox 样式字体与界面统一（下拉列表会用它）", data.get("combo_family") == TARGET,
          str(data.get("combo_family")))
    check("使用的主题支持自定义字体（不是会忽略字体的原生主题）",
          data.get("theme") in ("clam", "default", "alt"), str(data.get("theme")))

    settings.shutdown()
    config.update(**{k: original[k] for k in original})
    service.unregister_hotkey()
    time.sleep(0.3)

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
