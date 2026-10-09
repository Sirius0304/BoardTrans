# -*- coding: utf-8 -*-
"""
排查「设置里删除模型后，磁盘上文件仍在」的问题。

做法：先把 zh_en 完整备份出来，然后分两种场景实测删除：
  场景 A：引擎**没有**加载过该模型  → 删除应该顺利
  场景 B：引擎**加载过**该模型（先在内存里加载，再卸载，再删）→ 复现用户的场景
每测完一种场景就恢复备份，保证不影响正常使用。

关键观察点：
  - packages/<key>/ 是否真的消失
  - packages_cache/<key>.argosmodel 是否真的消失
  - models.remove() 抛什么异常
  - is_installed() 是不是变成了 False（这决定了界面上显示"已移除"还是"已下载"）
"""
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, paths   # noqa: E402

KEY = "zh_en"
PKG_DIR = models.model_dir(KEY)
CACHE_FILE = os.path.join(paths.models_dir(), f"{KEY}.argosmodel")
BACKUP = r"F:\键译_备份_20260927\排查删除问题_zh_en"


def state(label):
    pkg = os.path.isdir(PKG_DIR)
    pkg_files = sum(len(fs) for _d, _s, fs in os.walk(PKG_DIR)) if pkg else 0
    pkg_size = models.installed_size(KEY) / 1024 / 1024 if pkg else 0
    cache = os.path.exists(CACHE_FILE)
    cache_size = os.path.getsize(CACHE_FILE) / 1024 / 1024 if cache else 0
    installed = models.is_installed(KEY)
    print(f"  [{label}]")
    print(f"     packages/{KEY}/      : {'存在' if pkg else '不存在'}"
          f"{f'（{pkg_files} 个文件, {pkg_size:.1f} MB）' if pkg else ''}")
    print(f"     cache/{KEY}.argosmodel: {'存在' if cache else '不存在'}"
          f"{f'（{cache_size:.1f} MB）' if cache else ''}")
    print(f"     is_installed()       : {installed}")
    return pkg, cache, installed


def do_backup():
    if os.path.exists(BACKUP):
        shutil.rmtree(BACKUP, ignore_errors=True)
    os.makedirs(BACKUP, exist_ok=True)
    if os.path.isdir(PKG_DIR):
        shutil.copytree(PKG_DIR, os.path.join(BACKUP, "packages_" + KEY))
    if os.path.exists(CACHE_FILE):
        shutil.copy2(CACHE_FILE, os.path.join(BACKUP, f"{KEY}.argosmodel"))
    print(f"  （已备份到 {BACKUP}）")


def do_restore():
    bpkg = os.path.join(BACKUP, "packages_" + KEY)
    bfile = os.path.join(BACKUP, f"{KEY}.argosmodel")
    if os.path.isdir(PKG_DIR):
        shutil.rmtree(PKG_DIR, ignore_errors=True)
    if os.path.isdir(bpkg):
        shutil.copytree(bpkg, PKG_DIR)
    if not os.path.exists(CACHE_FILE) and os.path.exists(bfile):
        shutil.copy2(bfile, CACHE_FILE)
    print(f"  （已恢复）")


def main():
    print("=" * 72)
    print("  排查：删除模型后磁盘文件是否真的被清理")
    print("=" * 72)

    print("\n  第 0 步：记录当前状态 + 备份")
    state("删除前")
    do_backup()

    # ---------------- 场景 A：引擎没加载过 ----------------
    print()
    print("=" * 72)
    print("  场景 A：引擎没有加载过该模型（冷删除）")
    print("=" * 72)
    try:
        freed = models.remove(KEY)
        print(f"  models.remove() 返回: {freed/1024/1024:.1f} MB")
        ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"  models.remove() 抛出: {type(exc).__name__}: {exc}")
        ok = False
    pkg, cache, installed = state("删除后")
    a_clean = (not pkg) and (not cache) and (not installed)
    print(f"  → 场景 A 清理{'干净' if a_clean else '不干净'}"
          f"{'' if a_clean else '（还有残留！）'}")

    print("\n  （恢复备份，准备下一场景）")
    do_restore()
    time.sleep(1)

    # ---------------- 场景 B：引擎加载过再卸载 ----------------
    print()
    print("=" * 72)
    print("  场景 B：引擎加载过该模型 → 卸载 → 再删除（模拟用户实际路径）")
    print("=" * 72)
    from app.engine import TranslationEngine
    eng = TranslationEngine()
    t0 = time.time()
    eng.load_model(KEY)
    print(f"  已加载模型（{time.time()-t0:.2f}s），内存中的模型: {eng.loaded_keys()}")

    eng.unload_all()
    print(f"  已调用 unload_all()，内存中的模型: {eng.loaded_keys()}")
    del eng
    import gc
    gc.collect()
    time.sleep(0.5)

    try:
        freed = models.remove(KEY)
        print(f"  models.remove() 返回: {freed/1024/1024:.1f} MB")
        ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"  models.remove() 抛出: {type(exc).__name__}: {exc}")
        ok = False
    pkg, cache, installed = state("删除后")
    b_clean = (not pkg) and (not cache) and (not installed)
    print(f"  → 场景 B 清理{'干净' if b_clean else '不干净'}"
          f"{'' if b_clean else '（还有残留！这就是问题所在）'}")

    print("\n  （恢复备份）")
    do_restore()
    time.sleep(1)
    state("恢复后")

    print()
    print("=" * 72)
    print("  结论")
    print("=" * 72)
    if a_clean and b_clean:
        print("  两种场景下删除都干净 —— 说明 models.remove() 本身没问题，")
        print("  问题可能出在界面层（没有真正调用删除，或调用后未校验结果）。")
    elif not b_clean:
        print("  场景 B（加载过模型再删）清理不干净 —— **文件被占用的嫌疑最大**，")
        print("  这是用户实际会走到的路径。")
    else:
        print("  场景 A 也不干净 —— 说明删除逻辑本身有问题。")
    print("=" * 72)


if __name__ == "__main__":
    sys.exit(main())
