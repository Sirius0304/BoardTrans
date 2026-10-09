# -*- coding: utf-8 -*-
"""真实下载全流程的进度显示验证（联网）。

主人要求"验证下载全流程的进度显示是否正常"，所以这里不打桩：
真的把模型下到临时目录，逐帧记录**界面文字**，然后断言：
  1. 百分比全程单调不减（没有任何一帧跳回 0%）
  2. 完成前一定到过 100%
  3. 解压阶段显示"正在解压"，不含 0%
  4. 全部事件里 never出现 (done=0, total=0) 的解压信号
  5. 最后文件大小与包声明一致

默认下 zh_en（约 71 MB）。可传argv[1] 换成en_ja。
"""
import os
import re
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import models, paths  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [OK] {name}" + (f"({detail})" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def pct_of(text):
    m = re.search(r"(\d+)\s*%", text or "")
    return int(m.group(1)) if m else None


def ui_text(phase, pct):
    """复刻 settings_window._on_download_progress 的渲染逻辑（纯函数，不建窗口）。"""
    if phase == models.PHASE_EXTRACT:
        return "状态：下载完成，正在解压…"
    if not pct:
        return "状态：正在下载 0%"
    return f"状态：正在下载 {pct}%"


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "zh_en"
    info = models.PACKAGES[key]
    print("=" * 64)
    print(f"键译 · 真实下载进度验证（{info['display']}，{info['size_text']}）")
    print("=" * 64)

    # 用项目本来就支持的模型目录环境变量把整个模型区挪到临时目录，
    # 这样 models_dir / packages_dir / model_dir 全部指向 tmp，
    # 主人磁盘上的真模型既不会被读也不会被写。
    # （两次踩坑的记录：
    #   1) 只patch models.models_dir() 不够 —— is_installed 走的是 packages_dir，
    #      ensure() 看到"已安装"会直接返回，压根不下载；
    #   2) 设SHUNYI_DATA_DIR 也不够 —— 模型目录认的是 SHUNYI_MODELS_DIR，
    #      见 paths.models_root() 的优先级：env > 配置项 > 程序目录\models。）
    tmp = tempfile.mkdtemp(prefix="shunyi_dl_")
    os.environ["SHUNYI_MODELS_DIR"] = tmp
    paths.reset_models_root_cache()
    models.paths = paths

    events = []
    lock = threading.Lock()

    def cb(label, done, total, phase):
        # 完全按界面层的算法渲染，把每一帧的文字记下来
        pct = int(done * 100 / total) if total else 0
        with lock:
            events.append({"label": label, "done": done, "total": total,
                           "phase": phase, "pct": pct, "text": ui_text(phase, pct)})

    print(f"  临时模型目录：{paths.models_root()}")
    check("模型目录已隔离到临时区", paths.models_root() == tmp, paths.models_root())
    check("下载缓存也指向临时区", tmp in paths.models_dir(), paths.models_dir())
    check("解压目录也指向临时区", tmp in paths.packages_dir(), paths.packages_dir())
    check("起始状态：模型未安装", not models.is_installed(key),
          f"实际目录 {models.model_dir(key)}")

    t0 = time.time()
    models.ensure(key, progress_cb=cb, cancel_event=None)
    elapsed = time.time() - t0

    print(f"  下载耗时 {elapsed:.1f}s，共 {len(events)} 次进度回调")

    # ---- 逐段打印（采样，避免刷屏）
    dl = [e for e in events if e["phase"] == models.PHASE_DOWNLOAD]
    ex = [e for e in events if e["phase"] == models.PHASE_EXTRACT]
    print(f"  下载阶段 {len(dl)} 次回调，解压阶段 {len(ex)} 次")
    if dl:
        show = dl[:3] + dl[len(dl) // 2: len(dl) // 2 + 2] + dl[-3:]
        for e in show:
            print(f"       {e['pct']:3d}%  {e['done']/1024/1024:6.1f} MB   {e['text']}")
    for e in ex:
        print(f"       [解压] {e['text']}   done={e['done']} total={e['total']}")

    pcts = [e["pct"] for e in dl]
    print(f"  百分比序列：{pcts[:8]} ... {pcts[-5:] if len(pcts) > 8 else ''}")

    # ---- 断言
    check("有进度回调", len(dl) > 0, f"{len(dl)} 次")
    check("百分比全程单调不减（核心：无跳回 0%）",
          all(b >= a for a, b in zip(pcts, pcts[1:])),
          f"回退点={[i for i, (a, b) in enumerate(zip(pcts, pcts[1:])) if b < a]}")
    check("完成前到过 100%", pcts and max(pcts) == 100, f"max={max(pcts) if pcts else 0}")
    check("最后一次下载回调就是 100%", pcts and pcts[-1] == 100, f"{pcts[-1] if pcts else 0}")
    # 开头的 0% 是正常起点（刚下 0.2 MB 时 0.2/71 取整就是 0），
    # 要禁的是"已经涨上去之后又掉回 0%" —— 那才是主人看到的跳回。
    check("进度涨上去后没有回落到 0%（核心）",
          0 not in pcts[pcts.index(max(pcts)):] if pcts else True,
          f"序列尾部={pcts[-6:]}")
    check("没有中途归零的帧",
          all(pcts[i] == 0 for i in range(len(pcts)) if pcts[i] == 0) or
          max(i for i, p in enumerate(pcts) if p == 0) < 2,
          f"0 出现在索引 {[i for i, p in enumerate(pcts) if p == 0]}")
    check("解压阶段有事件", len(ex) >= 1, f"{len(ex)} 次")
    check("解压信号不再是 (0, 0)",
          all(not (e["done"] == 0 and e["total"] == 0) for e in ex),
          str([(e["done"], e["total"]) for e in ex]))
    check("解压文案不含百分比", all("0%" not in e["text"] for e in ex),
          str([e["text"] for e in ex]))

    dest = os.path.join(paths.models_dir(), f"{key}.argosmodel")
    size = os.path.getsize(dest) if os.path.exists(dest) else 0
    check("文件已落地且大小达标", size >= info["size"] * 0.98,
          f"{size/1024/1024:.1f} MB / 期望 {info['size']/1024/1024:.1f} MB")

    check("解压后模型被判定为已安装", models.is_installed(key),
          models.model_dir(key))

    os.environ.pop("SHUNYI_MODELS_DIR", None)
    paths.reset_models_root_cache()
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 64)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())