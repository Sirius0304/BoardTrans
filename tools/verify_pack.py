# -*- coding: utf-8 -*-
"""核对打包产物里是否真的含最新改动（字节码级证据）。

为什么要专门写这个：光看 build\\键译_精简\\PYZ-00.pyz 的时间戳"晚于源码"
只能说明 PyInstaller 重编过，不能证明**改的那几行确实进了包**。
这里把 exe 里的 PYZ 解出来，直接在字节码常量池里找特征字符串。

用法：python tools/verify_pack.py键译.exe
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader  # noqa: E402

# (模块, 特征字符串, 说明)
CHECKS = [
    ("app.models", "PHASE_DOWNLOAD", "下载阶段常量"),
    ("app.models", "PHASE_EXTRACT", "解压阶段常量"),
    ("app.models", "_Progress", "进度上报器类"),
    ("app.models", "finish", "收尾强制 100%（修'停在98%'）"),
    ("app.models", "_adapt_progress", "旧回调适配层"),
    ("app.models", "正在解压", "解压文案"),
    ("app.settings_window", "phase", "界面按阶段分流"),
    ("app.settings_window", "_dl_high", "界面单调水位线"),
    ("app.settings_window", "正在解压", "解压阶段界面文案"),
    # 2026-10-04 胶囊截断修复
    ("app.ui_kit", "force_redraw", "内容变化后主动重算宽高"),
    ("app.ui_kit", "update_idletasks", "重算前刷新请求尺寸"),
    ("app.ui_kit", "pad_x", "横向内缩独立参数（修描边被盖）"),
    ("app.settings_window", "状态：下载完成，正在解压…", "解压文案（已去重复括号）"),
    # 2026-10-09 改名 瞬译/ShunYi → 键译/BoardTrans
    ("app.paths", "BoardTrans", "新标识 APP_NAME"),
    ("app.paths", "键译", "新中文名"),
    ("app.paths", "migrate_legacy_data", "旧数据目录自动迁移（改名后用户设置不丢）"),
    ("app.settings_window", "BoardTransBase", "新命名字体"),
    # 2026-10-11 修「单 exe 下载模型失败」
    ("app.models", "_ensure_parent", "下载前兜底建父目录（根治 Errno 2）"),
    ("app.models", "_move_dir_contents", "迁移逐项搬、不再删目标目录"),
    ("app.models", "_ssl_context", "HTTPS 用 certifi 的 CA 包校验"),
    # 2026-10-12 修「长文本只翻最后一段」「TXT 文件无法翻译」
    ("app.capture", "_capture_paragraph", "自动取词改为取整段"),
    ("app.capture", "PARAGRAPH_EXTEND_KEY", "一键扩段（Ctrl+Shift+Up）"),
    ("app.capture", "SELECTION_PROBE_TIMEOUT", "选区快速探测窗口（150ms）"),
    ("app.capture", "clipboard_seq", "剪贴板序列号零成本判定"),
    ("app.capture", "_copy_selection", "复制选中内容带重试"),
    ("app.engine", "ensure_loaded", "模型预热接口（与取词并行）"),
    ("app.service", "_warmup_engine_async", "按键后后台预热模型"),
    ("app.capture", "PARAGRAPH_MAX_LINES", "整段取词的行数上限"),
    ("app.config", "_migrate_old_defaults", "旧默认值平滑升级"),
    ("app.config", "20000", "max_chars 新默认值"),
    # 2026-10-14 修游戏场景（守望先锋删原文+贴旧剪贴板）
    ("app.injector", "PASTE_CONFIRM_WAIT", "粘贴确认等待（防游戏帧循环竞态）"),
    ("app.injector", "paste_confirm_wait", "粘贴等待按前台窗口自适应"),
    ("app.injector", "_foreground_is_fullscreen", "全屏窗口判定"),
    ("app.capture", "COPY_RETRY_SETTLE", "复制重试前等修饰键物理抬起"),
    ("app.service", "已把原文放回原处", "空译文回注原文（防误删）"),
]

# 反向检查：这些名字**不该**出现在包里（改动以"删代码"为主时用它）。
# 2026-10-05 删掉了弹窗卡片内的加粗标题与左侧语义色竖线，
# 竖线由 _accent_color() 取色 —— 该函数已删，包里若还能找到它就是没打进去。
# 2026-10-16 整体移除「翻译字数上限」功能 —— 相关符号若还在包里就是残留。
FORBIDDEN = [
    ("app.dialogs", "_accent_color", "弹窗左侧竖线取色函数（已随竖线一起删除）"),
    ("app.config", "clamp_translate_chars", "译文上限夹紧（功能已整体移除）"),
    ("app.engine", "_translate_capped", "截断翻译（功能已整体移除）"),
    ("app.engine", "TRUNCATION_MARK", "截断省略号（功能已整体移除）"),
    ("app.settings_window", "_build_length_section", "字数上限设置卡片（已删除）"),
    ("app.settings_window", "_validate_limit", "字数输入校验（已删除）"),
    ("app.settings_window", "翻译字数上限", "字数上限卡片标题（已删除）"),
]


def consts_of(code):
    """收集字节码里出现的所有名字与字符串常量。

    两个池都要看（踩过的坑）：
      - `co_consts` 放字符串字面量（"正在解压" 这类文案）
      - `co_names`   放被引用的名字（PHASE_EXTRACT、_dl_high 这类标识符）
    早先只查 co_consts，于是一批明明在包里的标识符被误报成"没进包"。
    """
    names, strings = set(), set()

    def walk(c):
        # 三个池都要看（每个都踩过坑）：
        #   co_names    模块级/全局名字     —— PHASE_EXTRACT、_dl_high
        #   co_varnames 局部变量名         —— phase（函数参数）
        #   co_consts   字符串字面量       —— "正在解压"
        names.update(c.co_names)
        names.update(c.co_varnames)
        for k in c.co_consts:
            if isinstance(k, str):
                strings.add(k)
            elif hasattr(k, "co_consts"):
                walk(k)
    walk(code)
    return names | strings


def main():
    exe = sys.argv[1] if len(sys.argv) > 1 else "键译.exe"
    exe = os.path.abspath(exe)
    print("=" * 60)
    print(f"核对打包产物：{exe}")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as td:
        # ZlibArchiveReader 只接受文件路径，得先落盘
        archive = CArchiveReader(exe)
        pyz_path = os.path.join(td, "PYZ.pyz")
        with open(pyz_path, "wb") as f:
            f.write(archive.extract("PYZ.pyz"))
        z = ZlibArchiveReader(pyz_path)

        cache = {}
        ok = True
        for module, needle, desc in CHECKS:
            if module not in cache:
                try:
                    cache[module] = consts_of(z.extract(module))
                except Exception as exc:  # noqa: BLE001
                    cache[module] = None
                    print(f"  [FAIL] 取出 {module} 失败: {exc}")
                    ok = False
            pool = cache[module]
            if pool is None:
                ok = False
                continue
            hit = any(needle in c for c in pool)
            print(f"  [{'OK' if hit else 'FAIL'}] {module:22s} {desc}  ({needle})")
            ok = ok and hit

        # 反向检查：确认已删除的东西不在包里
        for module, needle, desc in FORBIDDEN:
            pool = cache.get(module)
            if pool is None:
                try:
                    pool = cache[module] = consts_of(z.extract(module))
                except Exception as exc:  # noqa: BLE001
                    print(f"  [FAIL] 取出 {module} 失败: {exc}")
                    ok = False
                    continue
            gone = not any(needle in c for c in pool)
            print(f"  [{'OK' if gone else 'FAIL'}] {module:22s} {desc}  (不含 {needle})")
            ok = ok and gone

    # 体积与时间
    size_mb = os.path.getsize(exe) / 1024 / 1024
    print(f"\n  产物大小：{size_mb:.2f} MB")
    print(f"  产物时间：{os.path.getmtime(exe):.0f}")
    print("\n" + ("全部通过：最新改动确实在包内" if ok else "有项目未进包，需重新打包"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())