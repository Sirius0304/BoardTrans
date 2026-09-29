# -*- coding: utf-8 -*-
"""models_root 配置热生效：回归测试（2026-09-29 修复"手改配置必须重启"缺陷）。

核心断言：
  A. 程序内改配置（config.update）后，models_root() 立即返回新目录，无需重启
  B. 手工编辑 config.json（直接写文件，模拟记事本）后同样立即生效
  C. 另一个进程改配置（多实例场景）后同样立即生效
  D. 来源没变时走缓存：不再重复探测（探测要写文件，很慢，不能每次跑）
  E. 换回原值后恢复原目录；models_dir()/packages_dir() 等子目录跟着变
  F. reset_models_root_cache() 仍然可用（清缓存后重新探测）

用法：F:\\shunyi\\.venv\\Scripts\\python.exe tools\\test_models_root.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

from app import config, paths               # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


def main():
    original = dict(config.load())
    orig_root = paths.models_root()

    print("\n=== A. 程序内改配置 → 立即生效（无需重启/无需手动 reset）===")
    tmp = tempfile.mkdtemp(prefix="shunyi_mr_test_")
    # mkdtemp 返回的路径在 %TEMP% 下，纯 ASCII 且可写
    config.update(models_root=tmp)
    new_root = paths.models_root()
    check("config.update 后 models_root() 立即变为新目录",
          os.path.abspath(new_root) == os.path.abspath(tmp),
          f"{new_root} vs {tmp}")
    check("models_dir() 子目录也跟着变", os.path.abspath(paths.models_dir()).startswith(os.path.abspath(tmp)),
          paths.models_dir())

    print("\n=== B. 手工编辑 config.json（直接写文件）→ 立即生效 ===")
    tmp2 = tempfile.mkdtemp(prefix="shunyi_mr_test2_")
    path = paths.config_path()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["models_root"] = tmp2
    with open(path, "w", encoding="utf-8") as f:          # 模拟记事本保存
        json.dump(raw, f, ensure_ascii=False, indent=2)
    time.sleep(0.05)                                       # mtime 分辨率保险
    root2 = paths.models_root()
    check("手改文件后 models_root() 立即变为新目录",
          os.path.abspath(root2) == os.path.abspath(tmp2), f"{root2} vs {tmp2}")

    print("\n=== C. 另一个进程改配置（多实例）→ 本进程立即生效 ===")
    tmp3 = tempfile.mkdtemp(prefix="shunyi_mr_test3_")
    code = ("import sys; sys.path.insert(0, sys.argv[1]);"
            "from app import config; config.update(models_root=sys.argv[2])")
    probe = subprocess.run([sys.executable, "-c", code, PROJECT, tmp3],
                           capture_output=True, text=True)
    check("子进程写入配置成功", probe.returncode == 0, probe.stderr.strip()[-120:])
    root3 = paths.models_root()
    check("本进程 models_root() 立即跟随子进程的修改",
          os.path.abspath(root3) == os.path.abspath(tmp3), f"{root3} vs {tmp3}")

    print("\n=== D. 来源没变时走缓存（不重复探测）===")
    before = paths.models_root()
    # 数一下探测次数：包一层计数器
    calls = {"n": 0}
    real_probe = paths._is_usable_dir

    def counting(path):
        calls["n"] += 1
        return real_probe(path)

    paths._is_usable_dir = counting
    try:
        for _ in range(50):
            paths.models_root()
    finally:
        paths._is_usable_dir = real_probe
    check("连续取 50 次路径，零次探测（全走缓存）",
          calls["n"] == 0 and paths.models_root() == before,
          f"探测 {calls['n']} 次")

    print("\n=== E. 改回原值 → 恢复原目录 ===")
    config.update(models_root=original.get("models_root", ""))
    restored = paths.models_root()
    check("恢复为原来的模型目录", os.path.abspath(restored) == os.path.abspath(orig_root),
          f"{restored} vs {orig_root}")

    print("\n=== F. reset_models_root_cache() 仍可用 ===")
    paths.reset_models_root_cache()
    check("清缓存后能重新探测出同样的目录",
          os.path.abspath(paths.models_root()) == os.path.abspath(orig_root),
          paths.models_root())

    # 清理临时目录
    for d in (tmp, tmp2, tmp3):
        try:
            for fn in os.listdir(d):
                os.remove(os.path.join(d, fn))
            os.rmdir(d)
        except OSError:
            pass

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n=== 结果：{passed}/{len(RESULTS)} 项通过 ===")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  未通过：{name}  {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
