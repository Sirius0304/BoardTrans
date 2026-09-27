# -*- coding: utf-8 -*-
"""
瘦身执行脚本（A 组：零风险项）
=============================

安全策略：
1. **不做真删除**，而是把目标"移动"到同级备份目录 —— 文件还在，随时可移回来；
   同盘移动是瞬间改名，跨盘才真正复制。
2. 分 3 批执行，每批移动后立即复核（源已消失 + 备份体积与文件数一致）。
3. 任一批复核不通过就立刻停止，不再继续后面的批次。

备份目录：F:\\瞬译_备份_20260927\\
"""
import os
import shutil
import sys
import time

PROJECT = r"F:\瞬译"
BACKUP = r"F:\瞬译_备份_20260927"
LOCAL = os.path.join(os.environ["LOCALAPPDATA"], "ShunYi")

BATCHES = [
    ("第1批", os.path.join(PROJECT, "models", "zh_en"),
     os.path.join(BACKUP, "models_zh_en"), "早期测试解压失败的残留（程序从不读取）"),
    ("第2批", os.path.join(PROJECT, ".venv_old_nogui"),
     os.path.join(BACKUP, ".venv_old_nogui"), "旧虚拟环境（没有 tkinter，已被新版替换）"),
    ("第3批", os.path.join(LOCAL, "packages_cache"),
     os.path.join(BACKUP, "packages_cache"), "模型压缩包缓存（模型装好后不再使用）"),
]


def measure(path):
    """返回 (总字节数, 文件数)。"""
    if os.path.isfile(path):
        return os.path.getsize(path), 1
    total = count = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
                count += 1
            except OSError:
                pass
    return total, count


def main():
    os.makedirs(BACKUP, exist_ok=True)
    print("=" * 72)
    print(f"  备份目录：{BACKUP}")
    print("=" * 72)

    done, freed = [], 0
    for label, src, dst, why in BATCHES:
        print(f"\n---- {label} ----")
        if not os.path.exists(src):
            print(f"  源不存在，跳过：{src}")
            continue
        if os.path.exists(dst):
            print(f"  备份目标已存在，跳过以免覆盖：{dst}")
            continue

        before_size, before_count = measure(src)
        print(f"  待处理：{src}")
        print(f"  体积 {before_size/1024/1024:.1f} MB，{before_count} 个文件  —— {why}")

        t0 = time.time()
        try:
            shutil.move(src, dst)
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] 移动出错，已停止后续批次：{type(exc).__name__} {exc}")
            break
        elapsed = time.time() - t0

        # ---- 复核 ----
        after_size, after_count = measure(dst)
        src_gone = not os.path.exists(src)
        size_match = (after_size == before_size)
        count_match = (after_count == before_count)
        print(f"  移动完成，用时 {elapsed:.1f}s")
        print(f"  [复核] 原位置已清空: {src_gone}")
        print(f"  [复核] 备份体积一致: {size_match}  ({after_size/1024/1024:.1f} MB)")
        print(f"  [复核] 备份文件数一致: {count_match}  ({after_count} 个)")

        if src_gone and size_match and count_match:
            done.append(label)
            freed += before_size
            print(f"  [通过] {label} 处理成功")
        else:
            print(f"  [不通过] 复核异常，立即停止，不再继续后面的批次！")
            break

    print()
    print("=" * 72)
    print(f"  已处理批次：{', '.join(done) if done else '无'}")
    print(f"  预计释放：{freed/1024/1024:.1f} MB")
    print(f"  全部内容都在备份目录里，可随时移回：{BACKUP}")
    print("=" * 72)


if __name__ == "__main__":
    sys.exit(main())
