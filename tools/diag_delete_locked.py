# -*- coding: utf-8 -*-
"""
排查第三轮：直接验证「模型正被加载时删除」会发生什么。

场景 E：模型正被引擎持有 → 直接删（不先卸载）→ 预期失败并残留
场景 F：先卸载，但删除前又被别的线程加载回来 → 模拟真实竞态
场景 G：正确流程（卸载 → 等待 → 删除）→ 预期干净

测完恢复备份。
"""
import os
import shutil
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, paths            # noqa: E402
from app.engine import TranslationEngine  # noqa: E402

KEY = "zh_en"
PKG_DIR = models.model_dir(KEY)
CACHE_FILE = os.path.join(paths.models_dir(), f"{KEY}.argosmodel")
BACKUP = r"F:\键译_备份_20260927\排查删除问题_zh_en"


def snap():
    return (os.path.isdir(PKG_DIR),
            sum(len(fs) for _d, _s, fs in os.walk(PKG_DIR)) if os.path.isdir(PKG_DIR) else 0,
            os.path.exists(CACHE_FILE),
            models.is_installed(KEY))


def show(tag, s):
    print(f"  [{tag}] packages/: {'有' if s[0] else '无'}({s[1]}文件)  "
          f"cache: {'有' if s[2] else '无'}  is_installed: {s[3]}")


def backup():
    if os.path.exists(BACKUP):
        shutil.rmtree(BACKUP, ignore_errors=True)
    os.makedirs(BACKUP, exist_ok=True)
    if os.path.isdir(PKG_DIR):
        shutil.copytree(PKG_DIR, os.path.join(BACKUP, "packages_" + KEY))
    if os.path.exists(CACHE_FILE):
        shutil.copy2(CACHE_FILE, os.path.join(BACKUP, f"{KEY}.argosmodel"))


def restore():
    bpkg = os.path.join(BACKUP, "packages_" + KEY)
    bfile = os.path.join(BACKUP, f"{KEY}.argosmodel")
    if os.path.isdir(PKG_DIR):
        shutil.rmtree(PKG_DIR, ignore_errors=True)
    if os.path.isdir(bpkg):
        shutil.copytree(bpkg, PKG_DIR)
    if not os.path.exists(CACHE_FILE) and os.path.exists(bfile):
        shutil.copy2(bfile, CACHE_FILE)


def try_remove(tag):
    try:
        freed = models.remove(KEY)
        print(f"  remove() 正常返回 {freed/1024/1024:.1f} MB")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  remove() 抛出 {type(exc).__name__}: {str(exc)[:90]}")
        return False


def main():
    backup()
    print("=" * 74)
    print("  直接验证：模型被加载时删除会怎样")
    print("=" * 74)
    print("  初始状态:")
    show("前", snap())

    # ---------------- 场景 E：模型正被引擎持有 ----------------
    print()
    print("=" * 74)
    print("  场景 E：模型正被引擎加载（未卸载）就删")
    print("=" * 74)
    eng = TranslationEngine()
    eng.load_model(KEY)
    print(f"  内存中: {eng.loaded_keys()}")
    try_remove("E")
    s = snap()
    show("删除后", s)
    e_clean = not s[0] and not s[2]
    print(f"  → {'清理干净' if e_clean else '**有残留 → 文件占用确实会导致删除失败**'}")
    # 清理：把引擎里的引用放掉并通过一次翻译重建
    eng.unload_all()
    del eng
    import gc
    gc.collect()
    time.sleep(0.8)

    restore()
    time.sleep(1)

    # ---------------- 场景 F：卸载后又立刻被别的线程加载 ----------------
    print()
    print("=" * 74)
    print("  场景 F：先卸载，但删除执行前又被别的线程加载回来（真实竞态）")
    print("=" * 74)
    eng2 = TranslationEngine()
    eng2.load_model(KEY)
    eng2.unload_all()                       # 界面里的第一步：卸载
    print(f"  卸载后内存中: {eng2.loaded_keys()}")

    # 就在这一瞬间，另一个线程（比如用户按了快捷键 / 预加载）把模型加载回来
    eng2.load_model(KEY)
    print(f"  但紧接着被重新加载: {eng2.loaded_keys()}")
    try_remove("F")
    s = snap()
    show("删除后", s)
    f_clean = not s[0] and not s[2]
    print(f"  → {'清理干净' if f_clean else '**有残留 → 竞态会导致删除失败，但界面看起来像删掉了**'}")
    eng2.unload_all()
    del eng2
    gc.collect()
    time.sleep(0.8)

    restore()
    time.sleep(1)

    # ---------------- 场景 G：正确流程 ----------------
    print()
    print("=" * 74)
    print("  场景 G：卸载 + 等待句柄释放后再删（应当的做法）")
    print("=" * 74)
    eng3 = TranslationEngine()
    eng3.load_model(KEY)
    eng3.unload_all()
    gc.collect()
    time.sleep(0.5)
    try_remove("G")
    s = snap()
    show("删除后", s)
    g_clean = not s[0] and not s[2]
    print(f"  → {'清理干净' if g_clean else '有残留'}")
    del eng3
    gc.collect()

    restore()
    time.sleep(1)
    show("恢复后", snap())

    print()
    print("=" * 74)
    print("  结论")
    print("=" * 74)
    print(f"  场景 E（占用中直接删）: {'干净' if e_clean else '残留'}")
    print(f"  场景 F（卸载后被抢加载）: {'干净' if f_clean else '残留'}")
    print(f"  场景 G（卸载后稍等再删）: {'干净' if g_clean else '残留'}")
    if not e_clean or not f_clean:
        print()
        print("  → 根因确认：只要删除时模型文件仍被引擎持有，删除就会失败/残留。")
        print("    而界面此前只在删除前调用一次 unload_models()，")
        print("    若期间有别的路径把模型加载回来，就会**删除失败但仍显示已移除**。")
    print("=" * 74)


if __name__ == "__main__":
    sys.exit(main())
