# -*- coding: utf-8 -*-
"""失败路径与健壮性回归（2026-10-18，对应审计 P0-①② / P1-③④⑤）

覆盖：
  ① 模型缺失检查必须**排在取词之前**（否则缺模型时每按一次快捷键就删掉一段原文）
  ② 翻译抛异常时必须把原文回注原位置（此前会直接丢字）
  ③ 模型缓存只认"字节数完全一致"；坏包（BadZipFile）自动删掉重下一次
  ④ inject_text 返回 (是否可能成功, 方式说明)，回写失败不再一律报成功
  ⑤ 「取词成功」日志只打一次（此前新旧两行重复）
"""
import inspect
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import injector, models, service   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    print("=" * 70)
    print("失败路径与健壮性回归")
    print("=" * 70)

    handler = inspect.getsource(service.HotkeyService._handle_translate)

    print("\n[1] 缺模型：必须在取词（删原文）之前返回")
    i_missing = handler.find("missing_for_target")
    i_capture = handler.find("capture_selected_text(")
    check("_handle_translate 里有模型就绪检查", i_missing != -1, str(i_missing))
    check("模型检查排在取词之前",
          i_missing != -1 and i_capture != -1 and i_missing < i_capture,
          f"missing@{i_missing} < capture@{i_capture} = {i_missing < i_capture}")
    check("模型检查在预热之前（缺模型时不空跑加载）",
          i_missing < handler.find("_warmup_engine_async()"))
    blk = handler[handler.find("if missing:"):handler.find("if missing:") + 600]
    check("缺模型分支不调用 inject_text（没取过词，无需回注）",
          "inject_text" not in blk.split("# 1)")[0])

    print("\n[2] 翻译异常：必须回注原文")
    i_tr = handler.find("result = engine.translate")
    i_exc = handler.find("except Exception", i_tr) if i_tr != -1 else -1
    i_restore = handler.find("_restore_original", i_exc) if i_exc != -1 else -1
    check("translate 调用被 try/except 包住", i_tr != -1 and i_exc > i_tr,
          f"translate@{i_tr} < except@{i_exc}")
    check("except 分支里调用了 _restore_original", i_restore > i_exc,
          f"except@{i_exc} < restore@{i_restore}")
    check("空译文分支也走 _restore_original",
          "_restore_original" in handler[handler.find("if not result:"):][:400])
    check("_restore_original 存在且回注原文",
          "inject_text(text" in inspect.getsource(service.HotkeyService._restore_original))

    # 行为验证：把 inject_text 换成记录器，确认回注的确实是原文
    rec = []
    real_inject = service.inject_text
    service.inject_text = lambda t, m="paste", r=True: (rec.append((t, m)), (True, "test"))[1]
    try:
        svc = service.HotkeyService()
        svc._restore_original("用户刚才打的中文", {"inject_mode": "paste"})
    finally:
        service.inject_text = real_inject
    check("回注内容就是原文", bool(rec) and rec[0][0] == "用户刚才打的中文", str(rec[:1]))

    print("\n[3] 模型缓存：精确大小 + 坏包自愈")
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "x.argosmodel")
        open(f, "wb").write(b"A" * 1000)
        check("99% 不算完整（旧实现会放过）", models._cache_complete(f, 1010) is False)
        check("恰好等长才算完整", models._cache_complete(f, 1000) is True)
        check("超长也不算完整", models._cache_complete(f, 900) is False)
        check("文件不存在返回 False", models._cache_complete(os.path.join(td, "no"), 1) is False)

    calls = []
    real_dir, real_installed = models.paths.models_dir, models.is_installed
    real_download, real_install = models.download, models.install
    with tempfile.TemporaryDirectory() as td:
        models.paths.models_dir = lambda: td
        models.is_installed = lambda key: False

        def fake_download(key, progress_cb=None, cancel_event=None):
            calls.append("download")
            with open(os.path.join(td, f"{key}.argosmodel"), "wb") as fh:
                fh.write(b"PK-not-really-a-zip")
            return os.path.join(td, f"{key}.argosmodel")

        def fake_install(key, progress_cb=None):
            calls.append("install")
            if calls.count("install") == 1:
                raise zipfile.BadZipFile("File is not a zip file")
            return os.path.join(td, "packages", key)

        models.download, models.install = fake_download, fake_install
        try:
            out = models.ensure("zh_en")
        finally:
            models.paths.models_dir, models.is_installed = real_dir, real_installed
            models.download, models.install = real_download, real_install
        check("坏包只重下一次（下载 2 次 / 安装 2 次）",
              calls.count("download") == 2 and calls.count("install") == 2, str(calls))
        check("自愈后返回安装目录", out.endswith(os.path.join("packages", "zh_en")), out)

    print("\n[4] 回写结果可感知")
    sig = inspect.signature(injector.inject_text)
    check("inject_text 返回 (bool, str)",
          sig.return_annotation is not inspect.Signature.empty, str(sig.return_annotation))
    ok, way = injector.inject_text("")
    check("空文本返回 (False, 说明)", ok is False and bool(way), f"{ok}, {way!r}")
    # 注意：模块启用了 `from __future__ import annotations`，注解是**字符串**
    check("send_unicode_text 返回布尔",
          str(inspect.signature(injector.send_unicode_text).return_annotation) == "bool",
          str(inspect.signature(injector.send_unicode_text).return_annotation))

    real_send = injector.send_unicode_text
    injector.send_unicode_text = lambda t: False          # 模拟"目标程序吞键"
    try:
        ok2, way2 = injector.inject_text("hello", "type", True)
    finally:
        injector.send_unicode_text = real_send
    check("type 模式按键被吞时返回 False", ok2 is False, f"{ok2}, {way2!r}")
    check("失败时文案明确提到可能被吞", "吞" in way2, way2)

    # 剪贴板写入失败 → 降级 type，降级失败 → False
    real_set, real_get = injector.capture.set_clipboard_text, injector.capture.get_clipboard_text
    injector.capture.set_clipboard_text = lambda t: False
    injector.capture.get_clipboard_text = lambda: "old"
    injector.send_unicode_text = lambda t: False
    try:
        ok3, way3 = injector.inject_text("hi", "paste", True)
    finally:
        injector.capture.set_clipboard_text, injector.capture.get_clipboard_text = real_set, real_get
        injector.send_unicode_text = real_send
    check("剪贴板失败 + 降级失败 → False", ok3 is False, f"{ok3}, {way3!r}")

    print("\n[5] 日志不重复")
    # 只数 log.info 调用（注释里的"取词成功"不算）
    check("「取词成功」日志只打一次", handler.count('log.info("取词成功') == 1,
          str(handler.count('log.info("取词成功')))

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
