# -*- coding: utf-8 -*-
"""改名迁移与兼容性回归（2026-10-09 瞬译/ShunYi → 键译/BoardTrans）。

验证四件事：
  1. 新标识生效：APP_NAME=BoardTrans、数据目录为 %LOCALAPPDATA%\\BoardTrans
  2. 自动迁移：旧目录有 config.json、新目录没有时，配置被复制过来
  3. 不覆盖：新目录已有 config.json 时，绝不动它
  4. 环境变量兼容：新名 BOARDTRANS_* 生效，旧名 SHUNYI_* 仍然认

用子进程 + 临时 LOCALAPPDATA 隔离，避免污染真实数据目录。
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def run_probe(localappdata: str, extra_env: dict | None = None, code: str = "") -> str:
    """在隔离的 LOCALAPPDATA 下跑一段探针代码，返回 stdout。"""
    env = dict(os.environ)
    env["LOCALAPPDATA"] = localappdata
    env.pop("SHUNYI_DATA_DIR", None)
    env.pop("BOARDTRANS_DATA_DIR", None)
    env.pop("SHUNYI_MODELS_DIR", None)
    env.pop("BOARDTRANS_MODELS_DIR", None)
    env.update(extra_env or {})
    script = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from app import paths\n"
        "%s\n" % (ROOT, code)
    )
    r = subprocess.run([PY, "-c", script], capture_output=True, text=True,
                       env=env, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


def main():
    print("=" * 64)
    print("改名迁移与兼容性回归")
    print("=" * 64)

    # ---- 1. 新标识与默认数据目录
    with tempfile.TemporaryDirectory() as td:
        out = run_probe(td, code=(
            "print('APP_NAME=' + paths.APP_NAME)\n"
            "print('CN=' + paths.APP_NAME_CN)\n"
            "print('DATA=' + paths.data_dir())\n"
        ))
        check("APP_NAME 已改为 BoardTrans", "APP_NAME=BoardTrans" in out, out.strip()[:80])
        check("中文名已改为 键译", "CN=键译" in out)
        check("默认数据目录为 …\\BoardTrans",
              os.path.join(td, "BoardTrans").lower() in out.lower(),
              [l for l in out.splitlines() if l.startswith("DATA=")][:1])

    # ---- 2. 自动迁移：旧目录有配置，新目录为空
    with tempfile.TemporaryDirectory() as td:
        old = os.path.join(td, "ShunYi")
        os.makedirs(old)
        with open(os.path.join(old, "config.json"), "w", encoding="utf-8") as f:
            f.write('{"hotkey": "ctrl+alt+z", "target_lang": "ja"}')
        with open(os.path.join(old, "boardtrans.log"), "w", encoding="utf-8") as f:
            f.write("old log")
        os.makedirs(os.path.join(old, "models"))          # 应被跳过
        with open(os.path.join(old, "models", "x.bin"), "wb") as f:
            f.write(b"0" * 1024)

        out = run_probe(td, code="paths.data_dir()")
        new = os.path.join(td, "BoardTrans")
        cfg = os.path.join(new, "config.json")
        check("旧配置已迁移到新目录", os.path.isfile(cfg), out.strip()[-120:])
        if os.path.isfile(cfg):
            body = open(cfg, encoding="utf-8").read()
            check("迁移内容完整（hotkey 保留）", "ctrl+alt+z" in body, body[:60])
        check("旧目录的文件仍在（复制而非移动）",
              os.path.isfile(os.path.join(old, "config.json")))
        check("models 目录未被复制（体积考虑）",
              not os.path.isdir(os.path.join(new, "models")))

    # ---- 3. 不覆盖：新目录已有配置
    with tempfile.TemporaryDirectory() as td:
        old = os.path.join(td, "ShunYi")
        new = os.path.join(td, "BoardTrans")
        os.makedirs(old)
        os.makedirs(new)
        open(os.path.join(old, "config.json"), "w", encoding="utf-8").write(
            '{"hotkey": "OLD"}')
        open(os.path.join(new, "config.json"), "w", encoding="utf-8").write(
            '{"hotkey": "NEW"}')
        run_probe(td, code="paths.data_dir()")
        body = open(os.path.join(new, "config.json"), encoding="utf-8").read()
        check("新目录已有配置时不被旧配置覆盖", "NEW" in body and "OLD" not in body, body[:60])

    # ---- 4. 环境变量：新名生效 + 旧名兼容
    with tempfile.TemporaryDirectory() as td:
        custom = os.path.join(td, "CustomData")
        os.makedirs(custom)
        out = run_probe(td, {"BOARDTRANS_DATA_DIR": custom},
                        code="print('DATA=' + paths.data_dir())")
        check("新环境变量 BOARDTRANS_DATA_DIR 生效",
              f"DATA={custom}" in out, out.strip()[-100:])

    with tempfile.TemporaryDirectory() as td:
        custom = os.path.join(td, "LegacyData")
        os.makedirs(custom)
        out = run_probe(td, {"SHUNYI_DATA_DIR": custom},
                        code="print('DATA=' + paths.data_dir())")
        check("旧环境变量 SHUNYI_DATA_DIR 仍兼容",
              f"DATA={custom}" in out, out.strip()[-100:])

    # ---- 5. 新名优先于旧名
    with tempfile.TemporaryDirectory() as td:
        newd = os.path.join(td, "NewDir")
        oldd = os.path.join(td, "OldDir")
        os.makedirs(newd)
        os.makedirs(oldd)
        out = run_probe(td, {"BOARDTRANS_DATA_DIR": newd, "SHUNYI_DATA_DIR": oldd},
                        code="print('DATA=' + paths.data_dir())")
        check("新名与旧名同时存在时，新名优先", f"DATA={newd}" in out)

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
