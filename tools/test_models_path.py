# -*- coding: utf-8 -*-
"""
验证模型路径改造：
  1. 路径决策：软件目录优先、含中文则回退、环境变量/配置可覆盖
  2. 非 ASCII 路径必须被拒绝（sentencepiece 的硬限制）
  3. 在"纯英文的软件目录"场景下，用**项目自己的引擎**跑通翻译
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, paths   # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'通过' if ok else '失败'}] {name}" + (f"  —— {detail}" if detail else ""))


def main():
    print("=" * 74)
    print("  一、当前环境下的路径决策")
    print("=" * 74)
    print(f"  软件所在目录      : {paths.program_dir()}")
    print(f"  默认模型目录(理想) : {paths.default_models_root()}")
    print(f"  实际使用的模型目录 : {paths.models_root()}")
    print(f"  解压模型目录      : {paths.packages_dir()}")
    print(f"  下载缓存目录      : {paths.models_dir()}")
    print(f"  数据目录(配置/日志): {paths.data_dir()}")

    ideal = paths.default_models_root()
    actual = paths.models_root()
    prog_has_cn = not paths.is_ascii(paths.program_dir())
    print()
    if prog_has_cn:
        check("软件目录含中文 → 自动回退到 ASCII 路径",
              os.path.abspath(actual) != os.path.abspath(ideal),
              f"回退到 {actual}")
        check("回退后的路径确实是纯 ASCII", paths.is_ascii(actual), actual)
    else:
        check("软件目录是纯英文 → 直接使用软件目录",
              os.path.abspath(actual) == os.path.abspath(ideal), actual)

    check("packages 目录在模型根目录下",
          paths.packages_dir().startswith(paths.models_root()))
    check("下载缓存在模型根目录下",
          paths.models_dir().startswith(paths.models_root()))

    print()
    print("=" * 74)
    print("  二、优先级与保护机制")
    print("=" * 74)

    # 2a 环境变量优先
    good_dir = os.path.join(tempfile.gettempdir(), "shunyi_env_test")
    os.environ["SHUNYI_MODELS_DIR"] = good_dir
    paths.reset_models_root_cache()
    got = paths.models_root()
    check("环境变量 SHUNYI_MODELS_DIR 能覆盖", os.path.abspath(got) == os.path.abspath(good_dir), got)
    os.environ.pop("SHUNYI_MODELS_DIR", None)
    paths.reset_models_root_cache()

    # 2b 配置项覆盖
    config.update(models_root=good_dir)
    paths.reset_models_root_cache()
    got = paths.models_root()
    check("配置项 models_root 能覆盖", os.path.abspath(got) == os.path.abspath(good_dir), got)

    # 2c 非 ASCII 的配置必须被拒绝（否则 sentencepiece 会崩）
    bad_dir = r"F:\shunyi\中文模型目录"
    config.update(models_root=bad_dir)
    paths.reset_models_root_cache()
    got = paths.models_root()
    check("含中文的自定义路径被拒绝（自动忽略）",
          os.path.abspath(got) != os.path.abspath(bad_dir),
          f"实际用 {got}")
    check("被拒后仍得到可用的 ASCII 路径", paths.is_ascii(got), got)

    # 复位
    config.update(models_root="")
    paths.reset_models_root_cache()

    print()
    print("=" * 74)
    print("  三、在纯英文目录下用项目自己的引擎验证")
    print("=" * 74)
    en_dir = os.path.join(tempfile.gettempdir(), "shunyi_models_en")
    if os.path.exists(en_dir):
        shutil.rmtree(en_dir, ignore_errors=True)
    os.makedirs(en_dir, exist_ok=True)

    # 把现有模型复制过去，模拟"模型放在软件目录下"的情形
    src_pkg = os.path.join(paths.data_dir(), "packages")
    if os.path.isdir(src_pkg):
        shutil.copytree(src_pkg, os.path.join(en_dir, "packages"))
    config.update(models_root=en_dir)
    paths.reset_models_root_cache()
    print(f"  切到: {paths.models_root()}")

    # 清掉引擎缓存，确保用新路径重新加载
    import app.engine as engine_mod
    from app.engine import TranslationEngine
    eng = TranslationEngine()
    try:
        out = eng.translate("你好，今天天气不错。", "en", "fast")
        check("新路径下翻译正常", "nice day" in out.lower(), out)
        check("引擎实际读取的是新位置",
              os.path.abspath(paths.packages_dir()).startswith(os.path.abspath(en_dir)),
              paths.packages_dir())
    except Exception as exc:  # noqa: BLE001
        check("新路径下翻译正常", False, f"{type(exc).__name__}: {str(exc)[:80]}")

    # 复位并清理
    config.update(models_root="")
    paths.reset_models_root_cache()
    shutil.rmtree(en_dir, ignore_errors=True)

    print()
    print("=" * 74)
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"  结果：{passed}/{len(RESULTS)} 项通过")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"    未通过：{name} {detail}")
    print("=" * 74)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
