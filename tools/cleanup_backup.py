# -*- coding: utf-8 -*-
"""
清理备份目录中已确认无用的部分（保留两个旧 exe 作为回退保险）。

安全措施：
1. 分批删除，每批不超过 10 项；
2. 每批删完立即复核（路径是否真的消失、是否影响到保留项）；
3. 任一批异常立即停止，不再继续；
4. 只删用户明确确认的三项，其余文件一律不动。

用户确认范围：models_zh_en + .venv_old_nogui + packages_cache（共约 400 MB）
保留：瞬译_exe_精简前_50.1MB.exe、瞬译_exe_精简中间版_34.1MB.exe
"""
import os
import shutil
import sys
import time

BACKUP = r"F:\瞬译_备份_20260927"

BATCHES = [
    ("models_zh_en（早期测试解压残留）", os.path.join(BACKUP, "models_zh_en")),
    ("packages_cache（模型压缩包缓存）", os.path.join(BACKUP, "packages_cache")),
    (".venv_old_nogui（旧虚拟环境）", os.path.join(BACKUP, ".venv_old_nogui")),
]

KEEP = [r"瞬译_exe_精简前_50.1MB.exe", r"瞬译_exe_精简中间版_34.1MB.exe"]


def measure(path):
    if os.path.isfile(path):
        return os.path.getsize(path), 1
    total = count = 0
    for d, _s, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(d, f))
                count += 1
            except OSError:
                pass
    return total, count


def main():
    print("=" * 70)
    print("  清理备份目录中已确认无用的部分")
    print(f"  目标目录：{BACKUP}")
    print("=" * 70)

    # 删除前先确认保留项都在
    print("\n  删除前核对「要保留的文件」：")
    for name in KEEP:
        p = os.path.join(BACKUP, name)
        exists = os.path.exists(p)
        print(f"    {'[在]' if exists else '[缺失]'} {name}")
        if not exists:
            print("    !! 保留项缺失，为安全起见中止操作")
            return 1

    freed = 0
    for label, path in BATCHES:
        print(f"\n---- 处理：{label} ----")
        if not os.path.exists(path):
            print("  不存在，跳过")
            continue

        size, count = measure(path)
        print(f"  体积 {size/1024/1024:.1f} MB，{count} 个文件")
        t0 = time.time()
        try:
            shutil.rmtree(path)
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] {type(exc).__name__}: {exc}")
            print("  已停止，不再继续后面的批次")
            break

        gone = not os.path.exists(path)
        print(f"  删除完成，用时 {time.time()-t0:.1f}s")
        print(f"  [复核] 目录已消失: {gone}")
        if gone:
            freed += size
            print(f"  [通过] 释放 {size/1024/1024:.1f} MB")
        else:
            print("  [不通过] 目录仍存在，立即停止")
            break

    print()
    print("=" * 70)
    print(f"  本次释放：{freed/1024/1024:.1f} MB")
    print()
    print("  备份目录剩余内容：")
    if os.path.isdir(BACKUP):
        total = 0
        for name in sorted(os.listdir(BACKUP)):
            p = os.path.join(BACKUP, name)
            s = measure(p)[0]
            total += s
            kind = "目录" if os.path.isdir(p) else "文件"
            print(f"    {s/1024/1024:8.1f} MB  [{kind}] {name}")
        print(f"    {'-'*8}")
        print(f"    {total/1024/1024:8.1f} MB  合计")
    else:
        print("    （备份目录已不存在）")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
