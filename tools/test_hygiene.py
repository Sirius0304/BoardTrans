# -*- coding: utf-8 -*-
"""隐私与仓库卫生回归（2026-10-18）

锁住凭据/隐私排查后的几条整改，防止日后回退：
  ① 日志默认**不记录**用户原文（config.log_user_text=False），打开后才预览
  ② 打包 spec 不写死本机路径（按 SPEC 推导），别人克隆后能直接打包
  ③ 已删除的死函数 notify_user 不再回来
  ④ .gitignore 兜住运行时 config.json / logs（防止 BOARDTRANS_DATA_DIR=. 误提交）
  ⑤ 公开文档里不再出现开发机用户名路径
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, injector, service   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def main():
    print("=" * 70)
    print("隐私与仓库卫生回归")
    print("=" * 70)

    print("\n[1] 日志默认不打用户原文")
    check("config 默认关闭 log_user_text", config.DEFAULTS.get("log_user_text") is False,
          str(config.DEFAULTS.get("log_user_text")))
    off = service._preview("今天天气不错", 60, {"log_user_text": False})
    check("关闭时只显示字数", off == "<6 字，内容已隐藏>", off)
    check("关闭时绝不包含原文", "今天" not in off, off)
    on = service._preview("今天天气不错", 4, {"log_user_text": True})
    check("打开后恢复截断预览", on == "今天天气…", on)
    check("取词日志走 _preview", "_preview(text, 60" in read("app/service.py"))
    check("译文日志也走 _preview", "_preview(result, 30" in read("app/service.py"))

    print("\n[2] spec 不写死本机路径")
    for spec in ("键译_精简.spec", "自检_精简.spec"):
        src = read(spec)
        check(f"{spec} 不含盘符硬编码", not re.search(r'[A-Za-z]:\\\\', src), "")
        check(f"{spec} 按 SPEC 推导 PROJECT", "os.path.abspath(SPEC)" in src, "")
        check(f"{spec} 导入了 os", re.search(r"^import os$", src, re.M) is not None, "")

    print("\n[3] 死函数已清除")
    check("injector 不再有 notify_user", not hasattr(injector, "notify_user"), "")
    check("源码里也没有 notify_user", "def notify_user" not in read("app/injector.py"), "")

    print("\n[4] .gitignore 兜住运行时数据")
    gi = read(".gitignore")
    for pat in ("/config.json", "/logs/", "*.token", "*.pem"):
        check(f"忽略 {pat}", pat in gi, "")

    print("\n[5] 公开文档不再含开发机用户名路径")
    docs = [d for d in os.listdir(os.path.join(ROOT, "docs")) if d.endswith(".md")]
    offenders = []
    # 只判"身份信息"：C:\Users\<真实用户名> 与裸用户名；
    # 项目目录（如 F:\shunyi）是文档里的示例安装路径，不算隐私，允许保留。
    bad = re.compile(r"C:\\Users\\yz\d+|用户名是 `yz050`|\byz050\b(?!304|@)")
    for d in docs:
        text = read(os.path.join("docs", d))
        if bad.search(text):
            offenders.append(d)
    check("docs/*.md 无本机用户名", not offenders, str(offenders))

    print("\n[6] 托盘菜单：已删「使用说明」，其余项保持不变")
    tray_src = read("app/tray.py")
    check("菜单里没有「使用说明」", "使用说明" not in tray_src, "")
    check("_on_help 处理器已删除", "_on_help" not in tray_src, "")
    # 其余菜单项必须原样都在（删一项不能顺手改别的）
    for label in ("目标语言", "翻译档位", "启用翻译", "设置…", "打开配置文件",
                  "打开数据目录", "打开日志", "退出"):
        check(f"菜单项「{label}」仍在", f'MenuItem("{label}"' in tray_src
              or f'MenuItem(\n                "{label}"' in tray_src, "")
    check("分节符数量未变（3 处 refresh）", tray_src.count("refresh") >= 3,
          str(tray_src.count("refresh")))

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
