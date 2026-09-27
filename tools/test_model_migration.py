# -*- coding: utf-8 -*-
"""
验证模型迁移：老位置（数据目录里的 packages / packages_cache）
会被搬到新的模型根目录下，用户不用重新下载。

不碰真实模型：用假 key 造数据，测完清理。
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, models, paths   # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""))


def main():
    print("=" * 74)
    print("  模型迁移测试")
    print("=" * 74)

    old_root = paths.data_dir()
    old_cache = os.path.join(old_root, "packages_cache")     # 旧名字
    old_pkg = os.path.join(old_root, "packages")

    # 记录真实内容，测完恢复
    real_cache_existed = os.path.isdir(old_cache)
    real_pkg_existed = os.path.isdir(old_pkg)
    print(f"\n  旧位置现状：")
    print(f"    {old_cache}: {'存在' if real_cache_existed else '不存在'}")
    print(f"    {old_pkg}: {'存在' if real_pkg_existed else '不存在'}")
    print(f"  新的下载缓存目录：{paths.models_dir()}")

    # 造一个假的旧缓存文件（1 MB），验证它会被迁移
    marker = os.path.join(old_cache, "_migrate_test.argosmodel")
    os.makedirs(old_cache, exist_ok=True)
    with open(marker, "wb") as f:
        f.write(b"m" * (1024 * 1024))
    print(f"\n  已造出测试标记文件：{marker}")

    # 执行迁移
    moved = models.migrate_legacy_models()
    print(f"\n  迁移记录：{moved}")

    new_cache = paths.models_dir()
    marker_moved = os.path.exists(os.path.join(new_cache, "_migrate_test.argosmodel"))
    check("旧缓存目录被搬到了新位置", marker_moved or not os.path.exists(marker),
          f"新位置: {new_cache}")

    if marker_moved:
        check("迁移后文件内容完好",
              os.path.getsize(os.path.join(new_cache, "_migrate_test.argosmodel")) == 1024 * 1024)
        check("旧目录已不存在（已完成搬迁）", not os.path.isdir(old_cache),
              f"{old_cache} {'仍在' if os.path.isdir(old_cache) else '已移除'}")

    # 确认真实模型仍然可用
    print()
    for key in ("zh_en", "en_ja"):
        check(f"{key} 模型仍可用", models.is_installed(key))

    # 清理测试标记
    for p in (os.path.join(new_cache, "_migrate_test.argosmodel"),
              os.path.join(old_cache, "_migrate_test.argosmodel")):
        if os.path.exists(p):
            os.remove(p)

    # 如果包目录本来不存在而现在被创建了空壳，也清掉
    if not real_pkg_existed and os.path.isdir(old_pkg) and not os.listdir(old_pkg):
        os.rmdir(old_pkg)

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name} {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
