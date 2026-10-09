# -*- coding: utf-8 -*-
"""
键译 - 翻译引擎

技术路线：CTranslate2 + SentencePiece 直接加载 OPUS-MT（Marian）神经网络翻译模型。
之所以不直接依赖 argostranslate 库：它的依赖链会拉入 stanza -> torch（安装体积 2GB+），
而 .argosmodel 包内部本身就是标准的 CTranslate2 模型目录，直接加载即可，
运行时依赖从 GB 级降到几十 MB，打包出来的 exe 也小得多。

支持的模型目录布局（自动探测）：
1. Argos 包（中文→英文）：<dir>/sentencepiece.model  +  <dir>/model/{config.json,model.bin}
2. Marian CT2 直转目录（中日大模型）：<dir>/{source.spm,target.spm,model.bin,config.json}

两档翻译质量：
- fast（高速度）：beam_size=1，贪心解码，延迟最低
- quality（高质量）：beam_size=8 + length_penalty，延迟约为 fast 的 2.5 倍，长句更准

注意（实测结论）：英文→日文模型在 beam_size=1 时会出现严重错译
（"Hello, nice day." → "昨日は、お昼の夜を過ごして、"），beam>=2 才稳定，
因此日文方向的 beam 有下限保护 JA_MIN_BEAM。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

import ctranslate2
import sentencepiece

from . import paths

# 句子边界：中英文标点 + 换行。模型单次输入上限约 150~200 个 token，
# 长文本必须先切句，否则会被截断丢内容。
SENT_END_CHARS = "。！？!?；;…\n"
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;…\n])")

# 目标语言代码 -> pivot 中间语言
SUPPORTED_TARGETS = ("en", "ja")

# 质量档位 -> 解码参数
PRESETS: dict[str, dict] = {
    "fast": {
        "beam_size": 1,
        "length_penalty": 1.0,
        "no_repeat_ngram_size": 0,
    },
    "quality": {
        "beam_size": 8,
        "length_penalty": 1.1,
        "no_repeat_ngram_size": 0,
    },
}

# 日文方向的安全下限（实测 beam=1 会崩）
JA_MIN_BEAM = 3


def split_sentences(text: str, max_len: int = 120) -> list[str]:
    """把长文本切成模型能处理的句子片段。

    先按句末标点切，再把过长的片段按逗号/顿号二次切分。
    """
    text = text.strip()
    if not text:
        return []
    raw = [s for s in _SENT_SPLIT_RE.split(text) if s and s.strip()]
    if not raw:
        raw = [text]

    out: list[str] = []
    for sentence in raw:
        sentence = sentence.strip()
        if len(sentence) <= max_len:
            out.append(sentence)
            continue
        # 二级切分：按中文/英文逗号、顿号、冒号
        parts = re.split(r"(?<=[，,、：:])\s*", sentence)
        buf = ""
        for part in parts:
            if not part:
                continue
            if len(buf) + len(part) <= max_len:
                buf += part
            else:
                if buf:
                    out.append(buf)
                # 极长无标点文本，硬切
                while len(part) > max_len:
                    out.append(part[:max_len])
                    part = part[max_len:]
                buf = part
        if buf:
            out.append(buf)
    return [s for s in out if s.strip()]


class MarianTranslator:
    """单个 Marian / OPUS-MT 模型（CTranslate2 格式）的封装。"""

    def __init__(self, model_root: str, threads: int = 0, compute_type: str = "int8", name: str = ""):
        self.name = name or os.path.basename(model_root)
        self.root = model_root
        self.sp_source, self.sp_target, self.ct2_dir, self.add_bos, self.add_eos = self._probe_layout(model_root)

        t0 = time.time()
        self.source_sp = sentencepiece.SentencePieceProcessor(model_file=self.sp_source)
        self.target_sp = (
            self.source_sp
            if (not self.sp_target or os.path.abspath(self.sp_target) == os.path.abspath(self.sp_source))
            else sentencepiece.SentencePieceProcessor(model_file=self.sp_target)
        )
        self.model = ctranslate2.Translator(
            self.ct2_dir,
            device="cpu",
            compute_type=compute_type,
            inter_threads=1,
            intra_threads=max(0, int(threads)),
        )
        self.load_seconds = time.time() - t0
        self._lock = threading.Lock()

    @staticmethod
    def _probe_layout(model_root: str):
        """探测模型目录布局，返回 (源 spm, 目标 spm, ct2 目录, add_bos, add_eos)。"""
        def has(folder, *names):
            return all(os.path.exists(os.path.join(folder, n)) for n in names)

        # 布局 1：Argos 包
        if os.path.exists(os.path.join(model_root, "sentencepiece.model")) and \
                os.path.exists(os.path.join(model_root, "model", "model.bin")):
            cfg_path = os.path.join(model_root, "model", "config.json")
            cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            return (
                os.path.join(model_root, "sentencepiece.model"),
                None,
                os.path.join(model_root, "model"),
                bool(cfg.get("add_source_bos", False)),
                bool(cfg.get("add_source_eos", True)),
            )

        # 布局 2：Marian CT2 直转目录
        if has(model_root, "model.bin") and (has(model_root, "source.spm") or has(model_root, "vocab.json")):
            sp_source = os.path.join(model_root, "source.spm")
            if not os.path.exists(sp_source):
                sp_source = os.path.join(model_root, "vocab.json")
            sp_target = os.path.join(model_root, "target.spm")
            if not os.path.exists(sp_target):
                sp_target = None
            cfg_path = os.path.join(model_root, "config.json")
            cfg = {}
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except (json.JSONDecodeError, OSError):
                    cfg = {}
            return (
                sp_source,
                sp_target,
                model_root,
                bool(cfg.get("add_source_bos", False)),
                bool(cfg.get("add_source_eos", True)),
            )

        raise FileNotFoundError(f"无法识别的模型目录布局: {model_root}")

    def translate(
        self,
        text: str,
        beam_size: int = 1,
        length_penalty: float = 1.0,
        no_repeat_ngram_size: int = 0,
        max_decoding_length: int = 256,
    ) -> str:
        """翻译单段文本（不做分句，调用方负责切句）。"""
        text = text.strip()
        if not text:
            return ""
        tokens = self.source_sp.encode(text, out_type=str)
        if self.add_bos:
            tokens = ["<s>"] + tokens
        if self.add_eos:
            tokens = tokens + ["</s>"]
        kwargs = dict(
            beam_size=max(1, int(beam_size)),
            length_penalty=length_penalty,
            max_decoding_length=max_decoding_length,
            max_input_length=200,
        )
        if no_repeat_ngram_size:
            kwargs["no_repeat_ngram_size"] = no_repeat_ngram_size
        with self._lock:   # ctranslate2.Translator 非线程安全，串行调用
            results = self.model.translate_batch([tokens], **kwargs)
        hypothesis = [t for t in results[0].hypotheses[0] if t not in ("</s>", "<s>")]
        raw = self.target_sp.decode(hypothesis)
        return self._clean(raw)

    @staticmethod
    def _clean(text: str) -> str:
        """清理 SentencePiece 占位符与多余空白。"""
        text = text.replace("\u2581", " ")        # ▁ -> 空格
        text = text.replace("<unk>", "").replace("\u2047", "")
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    def translate_text(self, text: str, beam_size: int = 1, length_penalty: float = 1.0,
                       joiner: str = " ") -> str:
        """整段翻译：内部按句切分后逐句翻译，再用 joiner 拼回。

        joiner 对英文用空格，对日文用空串（日文句子之间不需要空格）。
        """
        sentences = split_sentences(text)
        if not sentences:
            return ""
        if len(sentences) == 1:
            return self.translate(sentences[0], beam_size=beam_size, length_penalty=length_penalty)
        parts = [self.translate(s, beam_size=beam_size, length_penalty=length_penalty) for s in sentences]
        return joiner.join(p for p in parts if p).strip()


class TranslationEngine:
    """对外统一入口：负责模型加载、档位切换、中→英 / 中→日（含中转）翻译。"""

    def __init__(self, threads: int = 0):
        self.threads = threads
        self._models: dict[str, MarianTranslator] = {}
        self._lock = threading.Lock()
        self.last_stats: dict = {}

    # ---------- 模型管理 ----------

    def model_root(self, key: str) -> str:
        """key: zh_en / en_ja / tcbig_zh_ja"""
        return os.path.join(paths.packages_dir(), key)

    def is_model_ready(self, key: str) -> bool:
        root = self.model_root(key)
        if not os.path.isdir(root):
            return False
        if os.path.exists(os.path.join(root, "model", "model.bin")):
            return True
        return os.path.exists(os.path.join(root, "model.bin"))

    def load_model(self, key: str) -> MarianTranslator:
        with self._lock:
            if key in self._models:
                return self._models[key]
            root = self.model_root(key)
            compute_type = "int8"
            if key == "tcbig_zh_ja":
                # 该模型以 float16 发布，CPU 上用 int8 推理更快，CT2 会在加载时自动量化
                compute_type = "int8"
            translator = MarianTranslator(root, threads=self.threads, compute_type=compute_type, name=key)
            self._models[key] = translator
            return translator

    def unload_all(self) -> None:
        """卸载全部已加载模型，释放内存与文件占用（删除模型文件前调用）。"""
        with self._lock:
            self._models.clear()
        import gc
        gc.collect()

    def loaded_keys(self) -> list[str]:
        """当前已加载进内存的模型 key（供空闲释放判断用）。"""
        with self._lock:
            return list(self._models.keys())

    # ---------- 翻译 ----------

    def translate(self, text: str, target_lang: str = "en", quality: str = "fast") -> str:
        """把中文文本翻译成目标语言，返回译文。

        fast    : zh->en 直连（beam=1）；zh->ja 走 zh->en->ja 中转，第二段 beam 有下限
        quality : zh->en 直连（beam=8 + 长度惩罚）；zh->ja 中转两段都用大 beam
        """
        text = (text or "").strip()
        if not text:
            return ""
        target_lang = target_lang if target_lang in SUPPORTED_TARGETS else "en"
        quality = quality if quality in PRESETS else "fast"
        preset = PRESETS[quality]
        t0 = time.time()

        if target_lang == "en":
            translator = self.load_model("zh_en")
            result = translator.translate_text(
                text, beam_size=preset["beam_size"], length_penalty=preset["length_penalty"]
            )
            route = "zh->en"
        else:
            result, route = self._translate_to_ja(text, preset)

        self.last_stats = {
            "route": route,
            "quality": quality,
            "seconds": round(time.time() - t0, 3),
            "chars": len(text),
        }
        return result

    def _translate_to_ja(self, text: str, preset: dict):
        """中文 -> 日文：经英文中转（zh->en->ja）。

        说明：Argos 官方索引里没有中→日直连包，只有 zh->en 与 en->ja，
        因此日文方向采用"先译成英文、再译成日文"的中转方案；
        高质量档把两段的 beam 都拉大，以补偿中转带来的质量损失。
        """
        zh_en = self.load_model("zh_en")
        en_ja = self.load_model("en_ja")
        english = zh_en.translate_text(
            text, beam_size=preset["beam_size"], length_penalty=preset["length_penalty"]
        )
        # 第二段 beam 有下限：实测 beam=1 时英文->日文会出现严重错译
        ja_beam = max(preset["beam_size"], JA_MIN_BEAM)
        japanese = en_ja.translate_text(english, beam_size=ja_beam, length_penalty=1.0, joiner="")
        return japanese, f"zh->en->ja(中转, beam={preset['beam_size']}/{ja_beam})"

    def route_preview(self, target_lang: str, quality: str) -> str:
        """给界面/日志用的路线描述（不实际加载模型）。"""
        if target_lang == "en":
            return "中→英 直连, " + ("高速度 beam=1" if quality == "fast" else "高质量 beam=8")
        return "中→日 经英文中转, " + ("beam=1/3" if quality == "fast" else "beam=8/8")


if __name__ == "__main__":   # 便于单独调试
    import sys

    eng = TranslationEngine()
    src = sys.argv[1] if len(sys.argv) > 1 else "你好，今天天气不错。"
    tgt = sys.argv[2] if len(sys.argv) > 2 else "en"
    ql = sys.argv[3] if len(sys.argv) > 3 else "fast"
    print(eng.translate(src, tgt, ql), eng.last_stats)
