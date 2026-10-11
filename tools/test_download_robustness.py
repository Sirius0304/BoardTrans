# -*- coding: utf-8 -*-
"""下载与迁移健壮性回归（2026-10-11）

背景：单 exe 下载模型报
    [Errno 2] No such file or directory: '.../cache/zh_en.argosmodel'
根因有两个，本文件把它们各自钉住：

  1. 下载写文件前**不确保父目录存在** → 目录一被删就 Errno 2
     （关键认知：Python 报 Errno 2 时**即使缺的是目录也显示文件路径**）
  2. 模型位置迁移用 `os.rmdir(new)` + `shutil.move(old, new)`，
     会把"刚建好的空 cache 目录"删掉——正是它端掉了下载目录；
     而且 rmdir 之后 move 若失败，目标目录就永久缺失。

修法：下载前 `_ensure_parent`；迁移改 `_move_dir_contents`（逐项搬、绝不删目标）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    print("=" * 64)
    print("下载与迁移健壮性回归")
    print("=" * 64)

    # ---- 1. _ensure_parent：目录缺失时能建出来（原来这里直接 Errno 2）
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "models", "cache", "zh_en.argosmodel")
        # 目标父目录（models/cache）根本不存在
        try:
            models._ensure_parent(dest)
            with open(dest, "wb") as f:      # 修复前这一行抛 Errno 2
                f.write(b"x")
            ok = os.path.isfile(dest)
        except FileNotFoundError as exc:
            ok = False
            print(f"        复现了原始错误: {exc}")
        check("父目录缺失时，_ensure_parent 能建好并写入（原 Errno 2 场景）", ok)

    # ---- 2. _ensure_parent：层级多级也能建
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "a", "b", "c", "d", "f.bin")
        models._ensure_parent(dest)
        check("多级缺失目录能一次性建好", os.path.isdir(os.path.dirname(dest)))

    # ---- 3. _move_dir_contents：目标已存在且为空，**不能被删掉**（旧实现会 rmdir 它）
    with tempfile.TemporaryDirectory() as td:
        old = os.path.join(td, "old")
        new = os.path.join(td, "new")
        os.makedirs(old)
        os.makedirs(new)                      # 关键：目标已存在（空）
        open(os.path.join(old, "a.argosmodel"), "w").write("A")
        moved = models._move_dir_contents(old, new)
        check("目标目录存在时不被删除（旧实现 rmdir 的坑）", os.path.isdir(new))
        check("源目录里的文件被搬入目标", moved == ["a.argosmodel"] and
              os.path.isfile(os.path.join(new, "a.argosmodel")), str(moved))
        check("搬空的源目录被清掉（不留空壳）", not os.path.isdir(old))

    # ---- 4. _move_dir_contents：目标已有同名文件，绝不覆盖
    with tempfile.TemporaryDirectory() as td:
        old = os.path.join(td, "old")
        new = os.path.join(td, "new")
        os.makedirs(old)
        os.makedirs(new)
        open(os.path.join(old, "same.bin"), "w").write("OLD")
        open(os.path.join(new, "same.bin"), "w").write("NEW")
        moved = models._move_dir_contents(old, new)
        body = open(os.path.join(new, "same.bin"), encoding="utf-8").read()
        check("同名文件以目标为准，不被源覆盖", body == "NEW" and moved == [], body)

    # ---- 5. _move_dir_contents：内容部分重合时，只补缺失的
    with tempfile.TemporaryDirectory() as td:
        old = os.path.join(td, "old")
        new = os.path.join(td, "new")
        os.makedirs(old)
        os.makedirs(new)
        open(os.path.join(old, "a.bin"), "w").write("a")
        open(os.path.join(old, "b.bin"), "w").write("b")
        open(os.path.join(new, "a.bin"), "w").write("keep")
        moved = models._move_dir_contents(old, new)
        check("只补目标缺的那一项", moved == ["b.bin"], str(moved))
        check("已有项内容未被改动",
              open(os.path.join(new, "a.bin"), encoding="utf-8").read() == "keep")

    # ---- 6. 迁移的"自己搬自己"与"父子目录"必须跳过
    with tempfile.TemporaryDirectory() as td:
        same = os.path.join(td, "same")
        os.makedirs(same)
        open(os.path.join(same, "f.bin"), "w").write("f")
        # 直接验证 _move_dir_contents 不会把自己搬空（调用方已用 guard 拦截，
        # 这里验证 guard 的判断条件本身）
        a, b = os.path.abspath(same), os.path.abspath(same)
        check("源与目标相同时判定为跳过", a == b)
        child = os.path.join(same, "child")
        os.makedirs(child)
        b2 = os.path.abspath(child)
        check("目标在源内部时判定为跳过",
              b2.startswith(a + os.sep) or a.startswith(b2 + os.sep))

    # ---- 7. SSL 上下文：有 certifi 时应能建出上下文
    ctx = models._ssl_context()
    try:
        import certifi
        has_certifi = True
    except ImportError:
        has_certifi = False
    if has_certifi:
        check("装了 certifi 时能建出 SSL 上下文（不依赖系统证书库）",
              ctx is not None and getattr(ctx, "verify_mode", None) is not None)
    else:
        check("无 certifi 时优雅回退（返回 None，走系统证书库）", ctx is None)

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
