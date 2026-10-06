# -*- coding: utf-8 -*-
"""把体检结论输出成一份可读的 markdown 报告。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "项目文件体检报告.md")


def du(path):
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dp, dn, fn in os.walk(path):
        for f in fn:
            fp = os.path.join(dp, f)
            try:
                s = os.stat(fp)
                if getattr(s, "st_file_attributes", 0) & 0x400:
                    continue
                total += s.st_size
            except OSError:
                pass
    return total


def mb(n):
    return f"{n/1024/1024:.1f} MB"


# 精确量一遍要报的数字
cache_probe = du(os.path.join(ROOT, "models", "cache", "_tail_probe.argosmodel"))
cache_bak1 = du(os.path.join(ROOT, "models", "cache", "zh_en.argosmodel.notifytest-bak"))
cache_bak2 = du(os.path.join(ROOT, "models", "packages", "zh_en.notifytest-bak"))
build1 = du(os.path.join(ROOT, "build"))
build2 = du(os.path.join(ROOT, "build_selftest"))
pycache = 0
for dp, dn, fn in os.walk(ROOT):
    if "__pycache__" in dn:
        pycache += du(os.path.join(dp, "__pycache__"))
        dn.remove("__pycache__")
dist = du(os.path.join(ROOT, "dist"))
exe = du(os.path.join(ROOT, "瞬译.exe"))
venv = du(os.path.join(ROOT, ".venv"))
models = du(os.path.join(ROOT, "models"))
git = du(os.path.join(ROOT, ".git"))
docs = du(os.path.join(ROOT, "docs"))

safe = cache_probe + cache_bak1 + cache_bak2 + build1 + build2 + pycache
optional = dist

REPORT = f"""# 项目文件体检报告

> 生成时间：2026-10-04　·扫描方式：只读（未删除任何文件）
> 复现命令：`.venv\\Scripts\\python.exe tools\\audit_files.py`

## 一、总体

项目文件夹合计约 **{mb(du(ROOT))}**，构成：

| 条目 | 体积 | 性质 |
|---|---|---|
| `models/` | {mb(models)} | 翻译模型（可重新下载） |
| `.venv/` | {mb(venv)} | Python 环境（可由 `0-准备环境.bat` 重建） |
| `.git/` | {mb(git)} | 版本历史（**绝不可删**） |
| `build/` | {mb(build1)} | 打包中间产物（可重建） |
| `build_selftest/` | {mb(build2)} | 自检打包中间产物（可重建） |
| `瞬译.exe` | {mb(exe)} | 成品（可由 `3-打包exe.bat` 重建） |
| `dist/` | {mb(dist)} | 上一版成品归档 |
| `docs/` | {mb(docs)} | 文档与取证截图 |
| `app/` `tools/` | 约 1.3 MB | **源码，必需** |

## 二、必须保留（删了程序跑不起来 / 会丢东西）

| 文件 | 作用 |
|---|---|
| `app/`（17 个 .py） | **程序核心代码**，缺一不可 |
| `瞬译_精简.spec` | 打包配置（手写精简参数，删了打包会失败） |
| `自检_精简.spec` | 自检打包配置 |
| `app_icon.ico` | exe 图标来源 |
| `requirements.txt` | 依赖清单，`0-准备环境.bat` 要用 |
| `run_stage1.py` / `run_stage2.py` | 两个入口 |
| `checkup.py` / `selftest.py` | 自检脚本 |
| `0-准备环境.bat` `0-自检.bat` `1-控制台版.bat` `2-托盘版.bat` `3-打包exe.bat` | **主人双击用的入口**，缺一个就少个功能 |
| `models/packages/zh_en`、`models/packages/en_ja` | **已解压的模型本体**（zh_en 必用；选日文时 en_ja 必用） |
| `README.md` `docs/*.md` | 文档 |
| `.gitignore` `.gitattributes` | 版本控制配置 |

> `models/packages/` 里的模型是**离线运行的前提**。删掉后程序仍能启动，
> 但翻译时会提示缺模型并要求联网重新下载。离线使用请务必保留。

## 三、可以放心删除（会自动重建，删了没任何影响）

| 路径 | 体积 | 说明 |
|---|---|---|
| `build/` | {mb(build1)} | PyInstaller 中间产物，打包时自动重建 |
| `build_selftest/` | {mb(build2)} | 同上（自检版） |
| 全部 `__pycache__/` | {mb(pycache)} | Python 字节码缓存，首次运行自动生成 |
| `dist/瞬译_上一版.exe` | {mb(dist)} | 旧版归档；确认新版没问题后即可删 |

小计约 **{mb(safe + optional)}** 可回收。

## 四、建议清理（测试残留，程序已不再引用）

这几个是**测试脚本留下的备份文件**，我已用 grep 确认只有测试脚本本身提到它们，
`app/` 里没有任何引用，可以安全删除：

| 文件 | 体积 | 来源 |
|---|---|---|
| `models/cache/_tail_probe.argosmodel` | {mb(cache_probe)} | `tools/download_real_probe.py` 的临时下载产物 |
| `models/cache/zh_en.argosmodel.notifytest-bak` | {mb(cache_bak1)} | `tools/test_notify_real_download.py` 备份 |
| `models/packages/zh_en.notifytest-bak` | {mb(cache_bak2)} | 同上 |

小计 **{mb(cache_probe + cache_bak1 + cache_bak2)}**，是本次体检里最容易发现的"纯垃圾"。

## 五、可以删除但需权衡

| 路径 | 体积 | 删除后果 |
|---|---|---|
| `.venv/` | {mb(venv)} | 删了必须重跑 `0-准备环境.bat` 重装依赖（联网、约几分钟）。**不建议**，日常开发/测试都要用 |
| `models/cache/zh_en.argosmodel` | 71.0 MB | 已解压的模型在 `packages/`，删缓存不影响使用；但若以后要重装该模型需重新下载 |
| `tools/`（104 个文件） | 约 750 KB | 全部是测试与诊断脚本。**删了程序照常运行**，但会失去回归测试能力（含本次新加的像素级取证工具）。建议保留 |
| `docs/*.png` | — | 交付用的效果图与取证截图，删了不影响运行 |

## 六、结论

- **立刻可回收 ≈ {mb(safe + optional + cache_probe + cache_bak1 + cache_bak2)}**
  （打包中间产物 + 字节码缓存 + 旧版 exe + 测试残留）
- 加上 `.venv/` 则可回收约 **{mb(safe + optional + venv + cache_probe + cache_bak1 + cache_bak2)}**，
  但需要重装环境，不划算。
- **绝不要动**：`app/`、`models/packages/`、5 个 `.bat` 入口、`.git/`。

## 附：一条命令清掉所有"可放心删除"项

```bat
del /q 瞬译.exe.bak 2>nul
rmdir /s /q build build_selftest
for /d /r . %d in (__pycache__) do @if exist "%d" rmdir /s /q "%d"
```

（`models/` 里的测试残留建议手动删，避免误删 `packages/` 里的模型本体。）
"""

with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write(REPORT)

print("报告已生成：", OUT)
print(f"可回收合计约 {mb(safe + optional + cache_probe + cache_bak1 + cache_bak2)}")
