# -*- coding: utf-8 -*-
"""
删除模型的完整测试（覆盖用户反馈的问题）：
  1. 管理的两处（packages/ + packages_cache/）是否都被清理
  2. **程序目录里的额外副本**（如离线备份 translate-xxx-1_9.argosmodel）是否也被清理
  3. 删除前能否列出全部待删位置（供界面展示）
  4. 删除后的残留检查是否准确
  5. 删除本身就失败时（模拟文件被占用）能否如实反馈而不是假报成功

全程用假 key + 假文件，不动真实模型。
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, paths   # noqa: E402

FAKE = "_deltest"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""))


def make_fake_model():
    """造一个完整的假模型：解压目录 + 下载缓存 + 程序目录里的额外副本。"""
    pkg = models.model_dir(FAKE)
    os.makedirs(os.path.join(pkg, "model"), exist_ok=True)
    with open(os.path.join(pkg, "model", "model.bin"), "wb") as f:
        f.write(b"x" * (2 * 1024 * 1024))
    with open(os.path.join(pkg, "sentencepiece.model"), "wb") as f:
        f.write(b"s" * 1024)
    with open(os.path.join(pkg, "metadata.json"), "w", encoding="utf-8") as f:
        f.write("{}")

    cache = os.path.join(paths.models_dir(), f"{FAKE}.argosmodel")
    with open(cache, "wb") as f:
        f.write(b"y" * (1024 * 1024))

    extra_dir = os.path.join(paths.program_dir(), "models")
    os.makedirs(extra_dir, exist_ok=True)
    extra = os.path.join(extra_dir, f"translate-{FAKE}-1_9.argosmodel")
    with open(extra, "wb") as f:
        f.write(b"z" * (1024 * 1024))
    return pkg, cache, extra


def cleanup_fake():
    shutil.rmtree(models.model_dir(FAKE), ignore_errors=True)
    for p in (os.path.join(paths.models_dir(), f"{FAKE}.argosmodel"),
              os.path.join(paths.program_dir(), "models", f"translate-{FAKE}-1_9.argosmodel")):
        if os.path.exists(p):
            os.remove(p)


def main():
    print("=" * 74)
    print("  删除模型 · 完整测试")
    print("=" * 74)
    cleanup_fake()
    pkg, cache, extra = make_fake_model()
    print("\n  已造出假模型的三处文件：")
    print(f"    packages/      : {pkg}")
    print(f"    packages_cache : {cache}")
    print(f"    程序目录副本   : {extra}")

    print("\n  === 1. 删除前能否列出全部待删位置 ===")
    locations = models.describe_locations(FAKE)
    paths_list = [p for p, _s in locations]
    print(f"    列出 {len(locations)} 处：")
    for p, s in locations:
        print(f"      · {p}（{s/1024/1024:.1f} MB）")
    check("能列出解压目录", any("packages" in p and FAKE in p for p in paths_list))
    check("能列出下载缓存", any(p.endswith(f"{FAKE}.argosmodel") for p in paths_list))
    check("**能列出程序目录里的额外副本**",
          any("translate-" in p for p in paths_list),
          "这是用户反馈问题的关键：以前程序看不到这份文件")

    print("\n  === 2. 执行删除，检查三处是否都被清理 ===")
    result = models.remove(FAKE)
    print(f"    释放 {result['freed']/1024/1024:.1f} MB，清理 {len(result['removed'])} 处")
    for p in result["removed"]:
        print(f"      ✓ {p}")
    if result["failed"]:
        for p, why in result["failed"]:
            print(f"      ✗ {p} — {why}")

    check("解压目录已删除", not os.path.exists(pkg))
    check("下载缓存已删除", not os.path.exists(cache))
    check("**程序目录里的额外副本已删除**", not os.path.exists(extra),
          "修复重点：以前这份 71MB 会一直留着")
    check("删除结果里记录了所有已删路径", len(result["removed"]) >= 3, str(len(result["removed"])))

    print("\n  === 3. 删除后的残留检查 ===")
    left = models.leftover_files(FAKE)
    check("残留检查为空（说明删干净了）", not left, str(left))

    print("\n  === 4. 删除失败时的如实反馈（打桩模拟文件被占用）===")
    cleanup_fake()
    make_fake_model()

    # 4a：解压目录删不掉（被占用）→ remove 必须抛出，调用方才能提示用户而不是假报成功
    real_rmtree = shutil.rmtree

    def failing_rmtree(path, *a, **k):
        raise PermissionError("模拟：文件正被占用")

    shutil.rmtree = failing_rmtree
    try:
        models.remove(FAKE)
        check("解压目录删不掉时 remove 会抛出 PermissionError", False, "竟然没抛异常")
    except PermissionError:
        check("解压目录删不掉时 remove 会抛出 PermissionError", True, "调用方据此提示用户")
    except Exception as exc:  # noqa: BLE001
        check("解压目录删不掉时 remove 会抛出 PermissionError", False,
              f"抛出的是 {type(exc).__name__}")
    finally:
        shutil.rmtree = real_rmtree

    # 4b：额外副本删不掉 → 应记录在 failed 里，而不是静默忽略
    real_remove = os.remove

    def failing_remove(path, *a, **k):
        if "translate-" in str(path):
            raise PermissionError("模拟：副本被占用")
        return real_remove(path, *a, **k)

    os.remove = failing_remove
    try:
        res = models.remove(FAKE)
        check("额外副本删不掉时记录在 failed 里（不静默忽略）",
              len(res["failed"]) >= 1, str(res["failed"])[:80])
        check("其余部分仍被正常删除（单个失败不影响整体）",
              not os.path.exists(models.model_dir(FAKE)))
        left = models.leftover_files(FAKE)
        check("残留检查能发现没删掉的那份", any("translate-" in p for p in left), str(left)[:80])
    finally:
        os.remove = real_remove
        cleanup_fake()

    print("\n  === 5. 确认真实模型未受影响 ===")
    check("中译英模型仍在", models.is_installed("zh_en"))
    check("中译日模型仍在", models.is_installed("en_ja"))
    # 离线部署用的模型备份已移到外部备份目录（避免被当成"副本"清理），这里确认它安然无恙。
    # 该目录是**外部**的、可能被主人清理掉 —— 不存在时无法验证，跳过而不是判失败
    # （2026-10-01：实测环境里它已被删，导致这条断言长期假失败）。
    backup_dir = r"F:\键译_备份_20260927\离线部署用模型包"
    if os.path.isdir(backup_dir):
        has_backup = any(f.lower().endswith(".argosmodel") for f in os.listdir(backup_dir))
        check("离线部署用的模型备份仍在（未被误删）", has_backup,
              "只删与目标 key 匹配的文件，不碰其它")
    else:
        print(f"  [跳过] 外部备份目录不存在（{backup_dir}）—— 该项仅在备份存在时可验证")

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name} {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
