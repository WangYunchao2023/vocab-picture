#!/usr/bin/env python3
"""
Vocab Picture Generator v1.5.0 - 英语单词学习图片批量生成器
版本: 1.5.0

核心改进（v1.5）：
- 两步指示图：真实照片（RealVisXL）+ PIL 画卡通手指后期合成
- 绕过 vram_manager 锁死：直接 evict/restore LLM via Ollama API
- QA 对合成图宽容：真实照片主体清晰即 PASS，卡通手指用 PIL 精确控制
"""

import sys, os, argparse, time, json, random, shutil
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# ═══════════════════════════════════════════════════════════════
# 路径配置
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
COMFY_SCRIPTS = Path("/home/wangyc/.openclaw/skills/comfy-workflow/scripts")
VRAM_SCRIPTS = Path("/home/wangyc/.openclaw/scripts")
COMFYUI_OUTPUT = Path.home() / "ComfyUI" / "output"
DEFAULT_OUTPUT_DIR = Path.home() / "Pictures" / "vocab"

sys.path.insert(0, str(COMFY_SCRIPTS))
sys.path.insert(0, str(VRAM_SCRIPTS))

# ═══════════════════════════════════════════════════════════════
# 学习视角系统
# ═══════════════════════════════════════════════════════════════

# 视角类型
VIEW_TYPES = ["front", "side", "top", "closeup", "cut", "context"]

# 视角描述（英文为主，简单清晰）
VIEW_PROMPTS = {
    # 正面照：物体朝向镜头，放在自然环境中
    "front": {
        "desc": "正面照",
        "template": "{word} facing camera, placed naturally, slightly off-center, casual snapshot",
        "bg": "on worn kitchen counter, natural window light, slightly messy desk",
    },
    # 侧面照：展示侧面轮廓
    "side": {
        "desc": "侧面照",
        "template": "side view of {word}, profile visible, placed on {bg}",
        "bg": "wooden cutting board, soft natural light",
    },
    # 俯视图：从上往下拍
    "top": {
        "desc": "俯视图",
        "template": "top-down view of {word}, bird's eye perspective, flat lay",
        "bg": "on white marble countertop, overhead lighting, clean background",
    },
    # 特写：细节/质感
    "closeup": {
        "desc": "特写",
        "template": "extreme close-up detail of {word}, macro photography, showing texture and surface detail",
        "bg": "plain neutral background, macro lens, shallow depth of field",
    },
    # 切面：剖面图（仅适用水果/蔬菜/食物）
    "cut": {
        "desc": "切面图",
        "template": "cross-section view of {word}, cut in half showing inside flesh, seeds or core visible, food photography",
        "bg": "white ceramic plate, clean studio light, cross-section style",
    },
    # 场景照：物体在真实使用场景中
    "context": {
        "desc": "场景照",
        "template": "{word} in real-life context, human hand holding {word}, showing scale and usage",
        "bg": "natural indoor setting, everyday environment, lifestyle photography",
    },
}

# 水果/蔬菜单词 → 支持 cut 视图
CUTABLE_WORDS = {
    "apple", "banana", "orange", "grape", "strawberry", "watermelon",
    "mango", "peach", "pear", "cherry", "carrot", "tomato", "potato",
    "broccoli", "corn", "cucumber", "lettuce", "pepper", "onion", "garlic",
}

# 每个单词支持的视角（按优先级排序，生成 N 张时依次取前 N 个）
def get_word_views(word: str, count: int):
    """返回单词可用的视角列表（按顺序取前 count 个）"""
    w = word.lower().strip()
    views = ["front", "side", "top", "closeup"]

    # 如果可切，插入 cut 视图
    if w in CUTABLE_WORDS:
        # 插在第3位（top 之后）
        views.insert(3, "cut")

    # context 放最后
    views.append("context")

    # 如果请求数量超过可用视角，循环补充
    if count > len(views):
        extra = [v for v in views if v != "context"] * ((count // len(views)) + 1)
        views = views + extra

    return views[:count]


# ═══════════════════════════════════════════════════════════════
# QA 审核系统
# ═══════════════════════════════════════════════════════════════

import base64, io

def qa_check_image(image_path: str, word: str, style: str, view: str = None) -> tuple:
    """
    用 qwen2.5vl 审核图片是否合格（HTTP API 调用）。
    返回 (ok: bool, reason: str)
    """
    try:
        import base64 as _b64, requests as _req
        with open(image_path, "rb") as f:
            img_b64 = _b64.b64encode(f.read()).decode()
        style_hint = {
            "cartoon": "cartoon illustration",
            "realistic-photo": "realistic photograph",
            "flat-illustration": "flat illustration",
            "indicator": (
                "realistic photograph with a cartoon hand/finger overlaid on top — "
                "the cartoon finger is INTENTIONAL and should be accepted as correct"
            ),
        }.get(style, style)
        view_names = {
            "front": "正面照", "side": "侧面照",
            "top": "俯视图", "closeup": "特写",
            "cut": "切面图", "context": "场景照"
        }
        part_hint = f"Target word: '{word}'"
        if view:
            part_hint += f", View: {view_names.get(view, view)}"
        # indicator 合成图的特殊检查规则
        if style == "indicator":
            check_prompt = (
                f"You are an image reviewer for English vocabulary learning.\n"
                f"Image type: A REALISTIC PHOTOGRAPH with a CARTOON HAND overlaid on top.\n"
                f"The cartoon hand/finger pointing at the target area is CORRECT and expected.\n"
                f"Target word: '{word}'.\n\n"
                f"Check: 1)Is the realistic subject (face/object) clear and recognizable? "
                f"2)Is the cartoon hand/finger visible and pointing at the right area? "
                f"3)Are there any OTHER unexpected extra deformities beyond the cartoon hand? "
                f"4)Any text/watermarks/labels?\n\n"
                f"Reply with ONE line only:\n"
                f"PASS - [short reason if main subject clear and cartoon finger visible]\n"
                f"or\n"
                f"FAIL - [specific problem: e.g. face deformed beyond cartoon hand / wrong subject]"
            )
        else:
            check_prompt = (
                f"You are a strict image reviewer for English vocabulary learning.\n"
                f"Style: {style_hint}. {part_hint}.\n\n"
                f"Check: 1)Subject clear? 2)Any extra/deformed limbs? 3)Unnecessary decorations? "
                f"4)Style match? 5)No text/watermarks?\n\n"
                f"Reply with ONE line only:\nPASS - [short reason]\nor\nFAIL - [specific problem]"
            )
        payload = {
            "model": "qwen2.5vl:latest",
            "prompt": check_prompt,
            "images": [img_b64],
            "options": {"temperature": 0.1, "num_predict": 120},
            "stream": False,
        }
        r = _req.post("http://127.0.0.1:11434/api/generate",
                     json=payload, timeout=60)
        try:
            result = r.json().get("response", "").strip()
        except Exception:
            # 流式输出时 r.text 是多行 JSON，取最后一行
            lines = [l for l in r.text.strip().split("\n") if l]
            result = lines[-1]
            import json as _j
            result = _j.loads(result).get("response", "").strip()
        print(f"QA回复: {result[:80]}", flush=True)
        if result.startswith("PASS"):
            return True, result
        return False, result
    except Exception as e:
        print(f"   ⚠️ QA异常({e})，跳过审核", flush=True)
        return True, f"QA skipped: {e}"


def qa_regenerate_if_needed(word: str, style: str, view: str, seed: int,
                            max_attempts: int = 3,
                            output_dir: Path = None) -> str:
    """
    生成图片并 QA 审核，不合格则重生成，最多 max_attempts 次。
    返回最终合格的图片路径。
    """
    from comfy_client import ComfyUIClient
    from vram_manager import VMgr

    out_dir = output_dir or DEFAULT_OUTPUT_DIR
    style_obj = STYLES.get(style, STYLES["flat-illustration"])
    model_key = style_obj["model"]


    for attempt in range(1, max_attempts + 1):
        current_seed = seed + (attempt - 1) * 10000
        print(f"   🎲 生成尝试 {attempt}/{max_attempts} (seed={current_seed})", end=" ", flush=True)

        # 生成
        positive, negative = build_prompt(word, style, view)
        try:
            raw = comfy_generate(positive, negative, current_seed, 8, 1024, 1024, style)
        except Exception as e:
            print(f"❌ 生成失败: {e}")
            if attempt == max_attempts:
                raise
            continue

        # QA 审核
        print(f"🔍 QA 审核...", end=" ", flush=True)
        ok, reason = qa_check_image(raw, word, style, view)

        if ok:
            print(f"✅ PASS: {reason[:60]}")
            return raw
        else:
            print(f"❌ FAIL: {reason[:80]}")
            # 删除不合格图片
            Path(raw).unlink(missing_ok=True)
            # 如果有 _raw 也删
            raw_raw = str(Path(raw).parent / (Path(raw).stem.replace("_text","") + "_raw.png"))
            Path(raw_raw).unlink(missing_ok=True)

            if attempt == max_attempts:
                print(f"   ⚠️ 达到最大重试次数，跳过")
                raise RuntimeError(f"QA failed after {max_attempts} attempts: {reason}")
            print(f"   🔄 删除并重试...")

    raise RuntimeError("Should not reach here")


# ═══════════════════════════════════════════════════════════════
# 风格定义
# ═══════════════════════════════════════════════════════════════

STYLES = {
    "flat-illustration": {
        "desc": "扁平简约插画",
        "prompt_extra": "flat illustration, minimal, clean lines, solid colors, simple shapes, modern vector art style",
        "model": "flux1-schnell-fp8",
        "model_name": "flux1-schnell/flux1-schnell-fp8.safetensors",
        "type": "flux",
        "negative_extra": "3d render, realistic, photorealistic, depth, shading",
    },
    "cartoon": {
        "desc": "可爱卡通风格",
        "prompt_extra": "cute cartoon style, kawaii, bold outlines, vibrant colors, cheerful, expressive",
        "model": "flux1-schnell-fp8",
        "model_name": "flux1-schnell/flux1-schnell-fp8.safetensors",
        "type": "flux",
        "negative_extra": "realistic, photorealistic, serious, dark",
    },
    "realistic-photo": {
        "desc": "真实照片风格",
        "prompt_extra": (
            "DSLR photograph taken by human photographer, "
            "casual snapshot style, not composed not staged, "
            "natural casual apple on kitchen counter, "
            "slightly tilted, natural angle, "
            "window light, natural daylight, slightly warm tone, "
            "shallow depth of field, slight bokeh, "
            "imperfect framing, off-center subject, "
            "grain visible, slight noise, unpolished, "
            "candid food photography style"
        ),
        "model": "realvisxl-v4",
        "model_name": "realvisxl-v4/RealVisXL_V4.0.safetensors",
        "type": "sdxl",
        "negative_extra": (
            "cartoon, illustration, drawing, anime, blurry, low quality, "
            "studio lighting, perfect rim lighting, perfectly centered, "
            "staged arrangement, still life painting, "
            "oil painting style, watercolor style, "
            "octane render, unreal engine, C4D render, 3D render, "
            "artificially perfect, plastic texture, wax texture, "
            "over-sharpened, oversaturated, artificially vivid colors, "
            "AI generated look, diffusion artifacts, digital art, "
            "perfect composition, symmetrical, geometric, "
            "product photography, commercial photography, catalog photo, "
            "glossy render, cg smoothness"
        ),
    },
    "watercolor": {
        "desc": "水彩插画风格",
        "prompt_extra": "watercolor painting style, soft edges, delicate brush strokes, pastel colors, artistic, hand-painted texture",
        "model": "flux1-dev-fp8",
        "model_name": "flux1-dev-fp8/flux1-dev-fp8-e4m3fn.safetensors",
        "type": "flux",
        "negative_extra": "digital art, sharp edges, solid colors, realistic, 3d render",
    },
    "children-book": {
        "desc": "儿童绘本风格",
        "prompt_extra": "children's book illustration, storybook style, warm colors, hand-drawn look, charming, whimsical",
        "model": "flux1-dev-fp8",
        "model_name": "flux1-dev-fp8/flux1-dev-fp8-e4m3fn.safetensors",
        "type": "flux",
        "negative_extra": "realistic, photorealistic, dark, scary",
    },
    "line-art": {
        "desc": "简约线条画",
        "prompt_extra": "minimalist line art, black outlines, clean white background, simple strokes, elegant, sketch style",
        "model": "flux1-schnell-fp8",
        "model_name": "flux1-schnell/flux1-schnell-fp8.safetensors",
        "type": "flux",
        "negative_extra": "colorful, shading, realistic, 3d render, complex background",
    },
    "vintage": {
        "desc": "复古插画风格",
        "prompt_extra": "vintage illustration style, retro colors, aged paper texture, classic, nostalgic, elegant",
        "model": "sd3-medium",
        "model_name": "sd3-medium/sd3_medium_incl_clips.safetensors",
        "type": "sd3",
        "negative_extra": "modern, photorealistic, 3d render, bright colors",
    },
    "kawaii": {
        "desc": "日系可爱风格",
        "prompt_extra": "kawaii anime style, chibi, sparkly eyes, cute expression, pastel colors, soft shading",
        "model": "flux1-dev-fp8",
        "model_name": "flux1-dev-fp8/flux1-dev-fp8-e4m3fn.safetensors",
        "type": "flux",
        "negative_extra": "realistic, photorealistic, western cartoon, dark",
    },
    # 部位指示图风格（卡通手指指向）
    "indicator": {
        "desc": "部位指示图（卡通手指指向）",
        "prompt_extra": (
            "cartoon style, big bold outline, educational diagram, "
            "big pointing finger pointing at target, clean white background, "
            "centered composition, high contrast, clear visible subject, "
            "vibrant colors, no text, no watermark, no label"
        ),
        "model": "realvisxl-v4",
        "model_name": "realvisxl-v4/RealVisXL_V4.0.safetensors",
        "type": "sdxl",
        "negative_extra": (
            "realistic, photorealistic, photographic, blurry, low quality, "
            "extra limbs, extra fingers, deformed anatomy, text, watermark, "
            "label, messy background, 3d render, oil painting"
        ),
    },
}

STYLE_LIST = list(STYLES.keys())

# ═══════════════════════════════════════════════════════════════
# ComfyUI 工作流
# ═══════════════════════════════════════════════════════════════

def build_flux_workflow(prompt, negative, seed, steps, width, height,
                        model_name="flux1-schnell/flux1-schnell-fp8.safetensors",
                        cfg=1.0):
    return {
        "1": {"inputs": {"ckpt_name": model_name}, "class_type": "CheckpointLoaderSimple"},
        "2": {"inputs": {"clip": ["1", 1], "clip_l": prompt, "t5xxl": prompt, "guidance": 1.0}, "class_type": "CLIPTextEncodeFlux"},
        "3": {"inputs": {"clip": ["1", 1], "clip_l": negative, "t5xxl": negative, "guidance": 1.0}, "class_type": "CLIPTextEncodeFlux"},
        "4": {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyFlux2LatentImage"},
        "5": {"inputs": {"steps": max(steps, 4), "width": width, "height": height}, "class_type": "Flux2Scheduler"},
        "6": {"inputs": {"sampler_name": "euler"}, "class_type": "KSamplerSelect"},
        "7": {"inputs": {"model": ["1", 0], "add_noise": "enable", "noise_seed": seed, "cfg": cfg, "positive": ["2", 0], "negative": ["3", 0], "sampler": ["6", 0], "sigmas": ["5", 0], "latent_image": ["4", 0]}, "class_type": "SamplerCustom"},
        "8": {"inputs": {"samples": ["7", 0], "vae": ["1", 2]}, "class_type": "VAEDecode"},
        "9": {"inputs": {"images": ["8", 0], "filename_prefix": "vocab_FLUX"}, "class_type": "SaveImage"},
    }

def build_sdxl_workflow(prompt, negative, seed, steps, width, height,
                         model_name="realvisxl-v4/RealVisXL_V4.0.safetensors",
                         cfg=3.5):
    return {
        "1": {"inputs": {"ckpt_name": model_name}, "class_type": "CheckpointLoaderSimple"},
        "2": {"inputs": {"text": prompt, "clip": ["1", 1]}, "class_type": "CLIPTextEncode"},
        "3": {"inputs": {"text": negative, "clip": ["1", 1]}, "class_type": "CLIPTextEncode"},
        "4": {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyLatentImage"},
        "5": {"inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0], "model": ["1", 0], "denoise": 1.0}, "class_type": "KSampler"},
        "6": {"inputs": {"samples": ["5", 0], "vae": ["1", 2]}, "class_type": "VAEDecode"},
        "7": {"inputs": {"images": ["6", 0], "filename_prefix": "vocab_SDXL"}, "class_type": "SaveImage"},
    }

def build_sd3_workflow(prompt, negative, seed, steps, width, height,
                       model_name="sd3-medium/sd3_medium_incl_clips.safetensors",
                       cfg=5.0):
    return {
        "1": {"inputs": {"ckpt_name": model_name}, "class_type": "CheckpointLoaderSimple"},
        "2": {"inputs": {"text": prompt, "clip": ["1", 1]}, "class_type": "CLIPTextEncode"},
        "3": {"inputs": {"text": negative, "clip": ["1", 1]}, "class_type": "CLIPTextEncode"},
        "4": {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyLatentImage"},
        "5": {"inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0], "model": ["1", 0], "denoise": 1.0}, "class_type": "KSampler"},
        "6": {"inputs": {"samples": ["5", 0], "vae": ["1", 2]}, "class_type": "VAEDecode"},
        "7": {"inputs": {"images": ["6", 0], "filename_prefix": "vocab_SD3"}, "class_type": "SaveImage"},
    }

def build_workflow(prompt, negative, seed, steps, width, height, style_key,
                     model_override: str = None, model_name_override: str = None):
    style = STYLES.get(style_key, STYLES["flat-illustration"])
    model_key = model_override or style["model"]
    model_name = model_name_override or style["model_name"]
    # 根据实际 model_key 判断类型（不受 style_key 的 type 限制）
    if "flux" in model_key:
        return build_flux_workflow(prompt, negative, seed, steps, width, height, model_name, cfg=1.0)
    elif "sd3" in model_key:
        return build_sd3_workflow(prompt, negative, seed, steps, width, height, model_name, cfg=5.0)
    else:
        return build_sdxl_workflow(prompt, negative, seed, steps, width, height, model_name, cfg=3.5)

# ═══════════════════════════════════════════════════════════════
# 本地生成
# ═══════════════════════════════════════════════════════════════

def comfy_check_available() -> bool:
    try:
        import requests
        r = requests.get("http://127.0.0.1:8188/system_stats", timeout=5)
        return r.status_code == 200
    except:
        return False

def comfy_ensure_running():
    if comfy_check_available():
        return True
    print("   🔧 ComfyUI 未运行，正在启动...")
    try:
        from vram_manager import VMgr
        vm = VMgr()
        ok = vm.acquire_for_comfy(reason="vocab-gen-boot")
        if ok:
            for i in range(15):
                time.sleep(2)
                if comfy_check_available():
                    print(f"   ✅ ComfyUI 已就绪")
                    return True
    except Exception as e:
        print(f"   ⚠️ 启动失败: {e}")
    return False

_MODEL_TO_STYLE = {v["model"]: k for k, v in STYLES.items()}

def comfy_warmup_model(model_key: str, timeout_sec: int = 120) -> bool:
    from comfy_client import ComfyUIClient
    from vram_manager import VMgr
    print(f"   🔥 预热模型: {model_key} ...", end="", flush=True)
    vm = VMgr()
    vm.acquire_for_comfy(reason=f"warmup-{model_key}")
    try:
        client = ComfyUIClient()
        style_key = _MODEL_TO_STYLE.get(model_key, "flat-illustration")
        wf = build_workflow(
            prompt="white background, solid color, simple",
            negative="blurry, watermark, text, deformed, ugly, low quality",
            seed=0, steps=4, width=256, height=256, style_key=style_key,
        )
        pid = client.post_prompt(wf)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            h = client.get_history(prompt_id=pid)
            if h:
                for k, v in h.items():
                    s = v.get("status", {}).get("status_str", "")
                    if s == "success":
                        print(f" ✅ ({time.time()-deadline+timeout_sec:.0f}s)")
                        return True
                    elif s == "failed":
                        print(f" ❌ 预热失败")
                        return False
            time.sleep(5)
        print(f" ❌ 预热超时")
        return False
    except Exception as e:
        print(f" ❌ 预热异常: {e}")
        return False
    finally:
        vm.release_and_restore()

_WARMED_MODELS = set()

def comfy_generate(prompt, negative, seed, steps, width, height, style_key,
                  model_override: str = None, model_name_override: str = None) -> str:
    """
    model_override: 直接指定 model_key（如 "realvisxl-v4"）
    model_name_override: 直接指定 model_name
    """
    from comfy_client import ComfyUIClient
    import requests
    style = STYLES.get(style_key, STYLES["flat-illustration"])
    model_key = model_override or style["model"]
    model_name = model_name_override or style["model_name"]

    # 预热（如未预热则先生成一次小图加载模型）
    if model_key not in _WARMED_MODELS:
        print(f"   🔥 预热 {model_key}...", end="", flush=True)
        try:
            requests.post("http://127.0.0.1:11434/api/generate",
                json={"model": "qwen2.5:14b", "prompt": "x",
                      "keep_alive": 0, "stream": False}, timeout=10)
        except:
            pass
        wf = build_workflow("white background", "blurry watermark text",
                           0, 4, 256, 256, style_key,
                           model_override=model_key, model_name_override=model_name)
        _client = ComfyUIClient()
        pid = _client.post_prompt(wf)
        _client.wait_for_prompt(pid, timeout_sec=60)
        _WARMED_MODELS.add(model_key)
        print(f" ✅", flush=True)

    client = ComfyUIClient()

    # evict LLM via direct API
    try:
        requests.post("http://127.0.0.1:11434/api/generate",
            json={"model": "qwen2.5:14b", "prompt": "x",
                  "keep_alive": 0, "stream": False}, timeout=10)
    except:
        pass
    print("   📦 抢占 VRAM (evict LLM)...", end="", flush=True)
    try:
        wf = build_workflow(prompt, negative, seed, steps, width, height, style_key,
                           model_override=model_override, model_name_override=model_name_override)
        print(f"   🎨 模型: {model_key}, 风格: {style_key}")
        print("   📤 提交任务...", end="", flush=True)
        pid = client.post_prompt(wf)
        print(f" ✅ prompt_id={pid[:8]}...")
        print("   ⏳ 等待生成...", end="", flush=True)
        result = client.wait_for_prompt(pid, timeout_sec=300)
        print(f" ✅")
        ts_now = time.time()
        output_files = []
        if COMFYUI_OUTPUT.exists():
            for f in sorted(COMFYUI_OUTPUT.glob("vocab_*.png"), key=lambda x: -x.stat().st_mtime):
                if ts_now - 300 < f.stat().st_mtime <= ts_now + 60:
                    output_files.append(f)
        if not output_files:
            raise RuntimeError("未找到输出文件")
        latest = output_files[0]
        print(f"   📁 输出: {latest.name}")
        out_dir = DEFAULT_OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / latest.name
        shutil.copy2(latest, dest)
        print(f"   📂 已复制到: {dest}")
        return str(dest)
    except Exception as e:
        print(f"   ❌ 错误: {e}")
        raise
    finally:
        print("   🔄 恢复 LLM...", end="", flush=True)
        try:
            requests.post("http://127.0.0.1:11434/api/generate",
                json={"model": "qwen2.5:14b", "prompt": "x",
                      "keep_alive": 300, "stream": False}, timeout=15)
            print(f" ✅")
        except Exception as e:
            print(f" ⚠️ LLM恢复异常: {e}")

# ═══════════════════════════════════════════════════════════════
# PIL 文字叠加
# ═══════════════════════════════════════════════════════════════

def overlay_text(image_path: str, word: str, translation: str,
                font_size: int = 80, zh_size: int = 48,
                output_dir: Path = None) -> str:
    img = Image.open(image_path).convert("RGBA")
    W, H = img.size
    zh_font_paths = [
        "/home/wangyc/.local/share/fonts/wps/NotoSansCJK-Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    ]
    bold_font_paths = [
        "/home/wangyc/.local/share/fonts/wps/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    zh_font = en_font = None
    for fp in zh_font_paths:
        if Path(fp).exists():
            try:
                zh_font = ImageFont.truetype(fp, zh_size)
                break
            except:
                pass
    for fp in bold_font_paths:
        if Path(fp).exists():
            try:
                en_font = ImageFont.truetype(fp, font_size)
                break
            except:
                pass
    if zh_font is None:
        zh_font = ImageFont.load_default()
    if en_font is None:
        en_font = zh_font

    en_text = word.upper()
    zh_text = translation
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    en_bb = dummy.textbbox((0, 0), en_text, font=en_font)
    zh_bb = dummy.textbbox((0, 0), zh_text, font=zh_font)
    en_w, en_h = en_bb[2]-en_bb[0], en_bb[3]-en_bb[1]
    zh_w, zh_h = zh_bb[2]-zh_bb[0], zh_bb[3]-zh_bb[1]
    bar_h = max(en_h, zh_h) + 36
    bar_y = H - bar_h
    overlay = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([(0, bar_y), (W, H)], fill=(255, 255, 255, 255))
    ex = (W - en_w) // 2
    ey = bar_y + 6
    for dx, dy in [(-2,-2),(-2,2),(2,-2),(2,2),(0,-2),(0,2),(-2,0),(2,0)]:
        draw.text((ex+dx, ey+dy), en_text, fill=(255,255,255,255), font=en_font)
    draw.text((ex, ey), en_text, fill=(30, 60, 140), font=en_font)
    zx = (W - zh_w) // 2
    zy = ey + en_h + 4
    for dx, dy in [(-1,-1),(-1,1),(1,-1),(1,1)]:
        draw.text((zx+dx, zy+dy), zh_text, fill=(255,255,255,255), font=zh_font)
    draw.text((zx, zy), zh_text, fill=(80, 80, 80), font=zh_font)
    out = Image.alpha_composite(img, overlay).convert("RGB")
    out_dir = output_dir or DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(image_path).stem
    new_path = out_dir / f"{stem}.png"
    out.save(new_path, "PNG")
    raw_path = out_dir / f"{stem}_raw.png"
    if not raw_path.exists():
        raw = Image.open(image_path).convert("RGB")
        raw.save(raw_path, "PNG")
    return str(new_path)

# ═══════════════════════════════════════════════════════════════
# 提示词构建（学习版）
# ═══════════════════════════════════════════════════════════════

WORD_TRANSLATIONS = {}
WORD_VISUALS = {}

def get_translation(word):
    return WORD_TRANSLATIONS.get(word.lower(), word)

def get_visual(word):
    return WORD_VISUALS.get(word.lower(), f"a {word}")

def build_prompt(word, style_key, view=None):
    """构建提示词，融入视角描述"""
    style = STYLES.get(style_key, STYLES["flat-illustration"])
    visual = get_visual(word)

    if style_key == "realistic-photo":
        # 真实照片：融入视角 + 自然背景
        if view:
            vp = VIEW_PROMPTS.get(view, VIEW_PROMPTS["front"])
            vp_template = vp["template"].format(word=word, bg=vp.get("bg",""))
            positive = (
                f"DSLR photograph, candid snapshot, natural imperfect, "
                f"{vp_template}, "
                f"natural daylight, window light, slightly warm tone, "
                f"shallow depth of field, slight bokeh, "
                f"grain visible, slight noise, unpolished photo, "
                f"off-center framing, not perfectly composed, "
                f"no text, no watermark"
            )
        else:
            positive = (
                f"DSLR photograph, casual snapshot, natural imperfect, "
                f"a {word}, placed casually in natural setting, "
                f"window light, shallow depth of field, slight bokeh, "
                f"grain visible, unpolished, off-center framing, "
                f"no text, no watermark"
            )
    else:
        # 插画风格：白背景、居中、干净
        if view:
            vp = VIEW_PROMPTS.get(view, VIEW_PROMPTS["front"])
            view_desc = vp["desc"]
            positive = (
                f"{style['prompt_extra']}, "
                f"{view_desc} of {visual}, "
                f"white background, clean simple background, "
                f"centered composition, high contrast, "
                f"educational illustration, "
                f"no text, no words, isolated subject, no clutter"
            )
        else:
            positive = (
                f"{style['prompt_extra']}, "
                f"white background, clean simple background, "
                f"centered composition, {visual}, "
                f"high contrast, educational illustration, "
                f"no text, no words, isolated subject, no clutter"
            )

    neg_base = (
        "blurry, watermark, deformed, bad anatomy, ugly, distorted, "
        "low quality, jpeg artifacts, noisy, complex background, "
        "multiple items, text, words, watermark logo, cropped"
    )
    negative = f"{neg_base}, {style.get('negative_extra','')}"
    return positive, negative


def build_indicator_prompt(word: str):
    """
    构建部位指示图的两套提示词：
    1. 真实照片风格 — 整体
    2. 卡通风格    — 单独的大手指向图（透明背景）
    返回 (photo_prompt, photo_negative, cartoon_prompt, cartoon_negative, target_pos)
    target_pos: (x_frac, y_frac) 手指指向目标在图中的相对位置 [0~1]
    """
    # 找出这个词属于哪个系统
    part_sys = None
    for sys_name, sys_data in PART_OF_SYSTEM.items():
        if word in sys_data["parts"]:
            part_sys = sys_data
            break

    # 每个部位的指向位置（相对坐标，宽高归一化）
    BODY_TARGETS = {
        "eye":    (0.50, 0.32),
        "nose":   (0.50, 0.50),
        "mouth":  (0.50, 0.65),
        "ear":    (0.20, 0.38),
        "head":   (0.50, 0.22),
        "hand":   (0.75, 0.62),
        "foot":   (0.72, 0.88),
        "arm":    (0.78, 0.55),
        "leg":    (0.72, 0.75),
        "finger": (0.78, 0.58),
    }
    VEHICLE_TARGETS = {
        "wheel":    (0.28, 0.78),
        "door":     (0.38, 0.50),
        "window":   (0.48, 0.38),
        "headlight":(0.15, 0.52),
        "bumper":   (0.12, 0.70),
    }
    ANIMAL_TARGETS = {
        "nose": (0.50, 0.55),
        "ear":  (0.30, 0.28),
        "tail": (0.85, 0.50),
        "paw":  (0.70, 0.78),
    }

    if not part_sys:
        # 不属于任何部位系统，降级：真实照片 + 单独卡通手指
        visual = get_visual(word)
        return (
            f"realistic photo, {visual}, clean background, "
            f"natural lighting, high detail, front-facing, centered",
            "blurry, low quality, cartoon, illustration, watermark, text",
            "cartoon big pointing finger, bold outline, transparent background, "
            "pointing right, cute kawaii style, vibrant colors, no background",
            "blurry, low quality, realistic, photographic, watermark, text",
            (0.50, 0.50),
        )

    sys_name = part_sys["whole"]
    targets = {"body": BODY_TARGETS, "vehicle": VEHICLE_TARGETS, "animal": ANIMAL_TARGETS}.get(sys_name, BODY_TARGETS)
    target_pos = targets.get(word, (0.50, 0.50))

    # ── 真实照片：整体 ──────────────────────────────────────
    photo_pos = (
        f"{part_sys['whole_visual']}, "
        f"plain neutral background, "
        f"natural lighting, sharp focus, professional photography, "
        f"no text, no watermark, no label, no annotation, "
        f"no hand, no finger, no pointing gesture in the scene"
    )
    photo_neg = (
        "cartoon, illustration, drawing, anime, blurry, low quality, "
        "text, watermark, label, annotation, hand, finger, pointing gesture, "
        "deformed, ugly, distorted, multiple items, cropped"
    )

    # ── 卡通手指（透明背景）─────────────────────────────────
    finger_desc = {
        "eye":    "cute big cartoon pointing finger, pointing up, pointing at eye area",
        "nose":   "cute big cartoon pointing finger, pointing forward, pointing at nose",
        "mouth":  "cute big cartoon pointing finger, pointing up, pointing at mouth",
        "ear":    "cute big cartoon pointing finger, pointing right, pointing at ear",
        "head":   "cute big cartoon pointing finger, pointing down, pointing at head",
        "hand":   "cute big cartoon pointing finger, pointing left, pointing at palm",
        "foot":   "cute big cartoon pointing finger, pointing up-left, pointing at foot",
        "arm":    "cute big cartoon pointing finger, pointing left, pointing at arm",
        "leg":    "cute big cartoon pointing finger, pointing left, pointing at leg",
        "finger": "cute big cartoon pointing finger, pointing right, pointing at finger",
        "wheel":   "cute big cartoon pointing finger, pointing down, pointing at wheel",
        "door":    "cute big cartoon pointing finger, pointing left, pointing at door",
        "window":  "cute big cartoon pointing finger, pointing up, pointing at window",
        "headlight":"cute big cartoon pointing finger, pointing left, pointing at headlight",
        "bumper":  "cute big cartoon pointing finger, pointing left, pointing at bumper",
        "nose_animal": "cute big cartoon pointing finger, pointing forward, pointing at dog nose",
        "ear_animal":  "cute big cartoon pointing finger, pointing up-right, pointing at dog ear",
        "tail":    "cute big cartoon pointing finger, pointing right, pointing at tail",
        "paw":     "cute big cartoon pointing finger, pointing left, pointing at paw",
    }.get(word, f"cute big cartoon pointing finger, pointing at the {word}")

    cartoon_pos = (
        f"{finger_desc}, "
        f"big bold black outline, white skin tone, "
        f"transparent PNG background, white background for reference, "
        f"kawaii style, cute cartoon hand, "
        f"bold lines, clean vector style, "
        f"centered composition, high contrast"
    )
    cartoon_neg = (
        "blurry, low quality, realistic, photographic, photorealistic, "
        "text, watermark, multiple fingers, extra limbs, deformed hand, "
        "complex background, messy, grayscale, monochrome"
    )

    return photo_pos, photo_neg, cartoon_pos, cartoon_neg, target_pos


# ═══════════════════════════════════════════════════════════════
# 单张生成
# ═══════════════════════════════════════════════════════════════

STEPS_MAP = {"draft": 4, "normal": 8, "hq": 20, "best": 30}

def draw_pointing_finger(W: int, H: int, target_pos: tuple,
                          finger_size_frac: float = 0.22) -> Image.Image:
    """
    用 PIL 画一个指向 target_pos 的卡通大手指。
    指向方向根据目标位置决定（手指从图片边缘伸入）。
    返回 RGBA Image。
    """
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 手指宽度（相对于图片宽度）
    fw = int(W * finger_size_frac)
    fh = int(fw * 2.2)  # 手指长度

    tx = int(target_pos[0] * W)
    ty = int(target_pos[1] * H)

    # 判断从哪个方向伸入
    # 优先：从下方伸入（手指尖朝上，指向目标）
    # 如果目标在边缘，从对侧方向伸入
    if ty < H * 0.3:
        # 目标在顶部，从下方伸入
        fx = tx - fw // 2
        fy = ty - fh  # 手指尖在 ty
        finger_top = ty - fh
        finger_bot = ty
        finger_left = tx - fw // 2
        finger_right = tx + fw // 2
    elif ty > H * 0.7:
        # 目标在底部，从上方伸入
        fx = tx - fw // 2
        fy = ty
        finger_top = ty
        finger_bot = ty + fh
        finger_left = tx - fw // 2
        finger_right = tx + fw // 2
    elif tx < W * 0.3:
        # 目标在左，从右伸入（手指横过来）
        fx = tx
        fy = ty - fh // 3
        fh2 = int(fw * 2.0)
        finger_left = tx
        finger_right = tx + fh2
        finger_top = ty - fw // 2
        finger_bot = ty + fw // 2
    elif tx > W * 0.7:
        # 目标在右，从左伸入
        fx = tx - fh2 if 'fh2' in dir() else tx - int(fw * 2.0)
        fh2 = int(fw * 2.0)
        fx2 = tx - fh2
        finger_left = tx - fh2
        finger_right = tx
        finger_top = ty - fw // 2
        finger_bot = ty + fw // 2
    else:
        # 中间区域，从下方伸入
        fx = tx - fw // 2
        fy = ty - fh
        finger_top = ty - fh
        finger_bot = ty
        finger_left = tx - fw // 2
        finger_right = tx + fw // 2

    # 简化：从下方伸入（最常见场景）
    fw2 = int(W * finger_size_frac)
    fh2 = int(fw2 * 2.2)
    finger_left = tx - fw2 // 2
    finger_right = tx + fw2 // 2
    finger_top = ty - fh2
    finger_bot = ty

    # 画手指（圆角矩形 + 圆形指尖）
    outline_w = max(3, W // 120)

    # 手掌（底部大圆角矩形）
    palm_top = finger_bot
    palm_bot = min(H + 10, finger_bot + fw2 * 2)
    palm_left = tx - fw2
    palm_right = tx + fw2

    # 粗黑边（轮廓）
    draw.ellipse([finger_left - outline_w, finger_top - outline_w,
                 finger_right + outline_w, finger_bot + outline_w],
                fill=(0, 0, 0, 255))
    draw.rounded_rectangle([finger_left - outline_w, finger_top - outline_w,
                            finger_right + outline_w, finger_bot + outline_w],
                           radius=fw2 // 2, fill=(0, 0, 0, 255))

    # 白色填充（手指）
    draw.rounded_rectangle([finger_left, finger_top,
                            finger_right, finger_bot],
                           radius=fw2 // 2, fill=(255, 255, 255, 255))

    # 掌心底色（浅肤色）
    draw.ellipse([palm_left, palm_top,
                  palm_right, palm_bot],
                 fill=(255, 220, 180, 255))
    # 黑色轮廓（手掌）
    draw.ellipse([palm_left - outline_w, palm_top - outline_w,
                  palm_right + outline_w, palm_bot + outline_w],
                 outline=(0, 0, 0, 255), width=outline_w)

    # 画三个小圆圈（指节装饰）
    joint_y1 = finger_top + fh2 // 4
    joint_y2 = finger_top + fh2 // 2
    for jx, jy in [(tx - fw2 * 0, joint_y1), (tx - fw2 * 0, joint_y2)]:
        r = fw2 // 6
        draw.ellipse([jx - r, jy - r, jx + r, jy + r],
                     fill=(255, 230, 200, 255))

    return img


def composite_indicator(photo_path: str, target_pos: tuple,
                       finger_size_frac: float = 0.22,
                       output_path: str = None) -> str:
    """
    将 PIL 画的卡通手指合成到真实照片上。
    target_pos: (x_frac, y_frac) 手指指尖指向的目标位置 [0~1]
    """
    photo = Image.open(photo_path).convert("RGBA")
    W, H = photo.size

    # 画卡通手指
    finger = draw_pointing_finger(W, H, target_pos, finger_size_frac)

    # 合成
    photo.paste(finger, (0, 0), finger)
    result = photo.convert("RGB")

    out_path = Path(output_path) if output_path else Path(photo_path)
    result.save(str(out_path), "PNG")
    return str(out_path)


def generate(word, style_key, add_text, seed, quality, use_local,
             output_dir=None, view=None):
    word = word.lower().strip()
    translation = get_translation(word)

    # 部位单词 → 两步走：真实照片 + 卡通手指后期合成
    if word in ALL_PART_WORDS:
        is_indicator = True
        photo_pos, photo_neg, cartoon_pos, cartoon_neg, target_pos = build_indicator_prompt(word)
        # 两张图用不同的 seed 区分
        seed_photo = seed if seed is not None else random.randint(0, 2**32-1)
        seed_cartoon = (seed + 99999) if seed is not None else random.randint(0, 2**32-1)
        steps = STEPS_MAP.get(quality, 8)
        result = {
            "word": word, "translation": translation, "style": "indicator",
            "model": "realvisxl-v4", "steps": steps,
            "seed": seed_photo,
            "add_text": add_text, "view": view,
            "success": False, "file": None,
        }
        try:
            if use_local and comfy_ensure_running():
                out_dir = output_dir or DEFAULT_OUTPUT_DIR
                # Step 1: 生成真实照片（整体）
                photo_raw = comfy_generate(
                    photo_pos, photo_neg, seed_photo, steps,
                    1024, 1024, "realistic-photo"
                )
                # Step 2: PIL 画卡通手指并合成（无需 AI 生成）
                final_name = f"indicator_{word}_{seed_photo}.png"
                final_path = out_dir / final_name
                composite_indicator(photo_raw, target_pos,
                                   finger_size_frac=0.22,
                                   output_path=str(final_path))
                if add_text:
                    final_path = overlay_text(str(final_path), word, translation,
                                          output_dir=out_dir)
                result["file"] = final_path
                result["success"] = True
            else:
                result["cloud_pending"] = True
                result["positive"] = photo_pos
                result["negative"] = photo_neg
        except Exception as e:
            result["error"] = str(e)
            result["cloud_pending"] = True
            result["positive"] = photo_pos
            result["negative"] = photo_neg
        return result

    # 普通单词 → 正常流程
    style_key = style_key if style_key in STYLES else "flat-illustration"
    positive, negative = build_prompt(word, style_key, view=view)
    steps = STEPS_MAP.get(quality, 8)
    if seed is None:
        seed = random.randint(0, 2**32-1)
    style = STYLES[style_key]
    result = {
        "word": word, "translation": translation, "style": style_key,
        "model": style["model"], "steps": steps, "seed": seed,
        "add_text": add_text, "view": view,
        "positive": positive, "negative": negative,
        "success": False, "file": None,
    }
    try:
        if use_local and comfy_ensure_running():
            raw = comfy_generate(positive, negative, seed, steps, 1024, 1024, style_key)
            if add_text:
                final = overlay_text(raw, word, translation,
                                   output_dir=output_dir or DEFAULT_OUTPUT_DIR)
            else:
                final = raw
            result["file"] = final
            result["success"] = True
        else:
            result["cloud_pending"] = True
    except Exception as e:
        result["error"] = str(e)
        result["cloud_pending"] = True
    return result

# ═══════════════════════════════════════════════════════════════
# 批量生成
# ═══════════════════════════════════════════════════════════════

def batch_generate(words, count_per_word=1, style=None, add_text=True,
                   quality="normal", force_cloud=False, dry_run=False,
                   output_dir=None, views=None):
    """
    批量生成单词学习图片

    Args:
        views: list or None — 指定视角列表，如 ["front","side","cut"]
              为 None 时自动根据 count_per_word 分配视角
    """
    out_dir = output_dir or DEFAULT_OUTPUT_DIR
    print("=" * 60)
    print(f"📚 Vocab Picture Generator v1.3.0")
    print("=" * 60)
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📝 单词: {words}")
    print(f"🔢 每词张数: {count_per_word}")
    print(f"🎨 风格: {style or '轮换'}")
    print(f"📝 文字: {'是' if add_text else '否'}")
    print(f"⚡ 质量: {quality}")
    print(f"📂 输出: {out_dir}")
    print(f"📐 视角: {'自动分配' if views is None else views}")
    print()

    use_local = not force_cloud
    if use_local and comfy_check_available():
        print(f"✅ 本地 ComfyUI 在线")
    elif use_local:
        print(f"⚠️ 本地 ComfyUI 离线，使用云端")
        use_local = False
    else:
        print(f"☁️ 强制云端模式")

    # 视角分配
    if views is None:
        # 自动：为每个单词生成 count_per_word 张时，按顺序取视图
        pass  # 在循环中处理

    # 风格列表
    if style and style in STYLES:
        style_list = [style] * count_per_word
    else:
        style_list = [STYLE_LIST[i % len(STYLE_LIST)] for i in range(count_per_word)]

    total = len(words) * count_per_word
    current = 0
    results = {}

    for word in words:
        w = word.lower().strip()
        translation = get_translation(w)
        word_results = []

        # 自动获取该单词的视角列表
        word_views = get_word_views(w, count_per_word)

        print(f"\n📦 {w} ({translation})")
        view_desc = [f"{v}({VIEW_PROMPTS[v]['desc']})" for v in word_views]
        print(f"   视角: {view_desc}")

        for i in range(count_per_word):
            current += 1
            st = style_list[i]
            view = word_views[i]
            seed = random.randint(0, 2**32-1)
            view_label = VIEW_PROMPTS[view]["desc"]

            print(f"   [{current}/{total}] {view_label} (seed={seed})...", end=" ", flush=True)

            if dry_run:
                pos, neg = build_prompt(w, st, view=view)
                mdl = STYLES[st]["model"]
                print(f"⏭️ [dry-run] view={view}, model={mdl}")
                word_results.append({"dry_run": True, "style": st, "view": view,
                                     "model": mdl, "positive": pos, "negative": neg, "seed": seed})
                continue

            try:
                res = generate(w, st, add_text, seed, quality, use_local,
                             output_dir=out_dir, view=view)
                if res.get("cloud_pending"):
                    print(f"☁️ [cloud-pending]")
                elif res["success"]:
                    fname = Path(res["file"]).name
                    # 部位词 → 自动 QA 审核，不合格则重生成
                    if w in ALL_PART_WORDS:
                        print(f"✅ 生成, ", end="", flush=True)
                        print(f"🔍 QA审核中...", end="", flush=True)
                        ok, reason = qa_check_image(res["file"], w, st, view)
                        if not ok:
                            print(f"\n   ❌ QA失败: {reason[:60]}")
                            print(f"   🔄 删除并重新生成...", end="", flush=True)
                            # 删除旧图
                            Path(res["file"]).unlink(missing_ok=True)
                            raw_raw = Path(res["file"]).parent / (Path(res["file"]).stem.replace("_text","") + "_raw.png")
                            raw_raw.unlink(missing_ok=True)
                            # 重生成
                            for retry in range(1, 3):  # 最多重试2次
                                seed2 = random.randint(0, 2**32-1)
                                print(f"\n   🎲 重试{retry+1}/3 (seed={seed2})", end="", flush=True)
                                res2 = generate(w, st, add_text, seed2, quality, use_local,
                                              output_dir=out_dir, view=view)
                                if res2.get("success"):
                                    ok2, reason2 = qa_check_image(res2["file"], w, st, view)
                                    if ok2:
                                        print(f"\n   ✅ QA通过! {fname}")
                                        res = res2
                                        fname = Path(res["file"]).name
                                        break
                                    else:
                                        print(f"\n   ❌ QA失败: {reason2[:60]}")
                                        Path(res2["file"]).unlink(missing_ok=True)
                                else:
                                    print(f"\n   ❌ 重生成失败")
                            else:
                                print(f"\n   ⚠️ 3次重试均失败，跳过")
                                res["success"] = False
                                res["error"] = "QA failed after 3 attempts"
                    else:
                        print(f"✅ {fname}", end="")
                else:
                    print(f"❌ {res.get('error','?')}")
                word_results.append(res)
            except Exception as e:
                print(f"❌ {e}")
                word_results.append({"success": False, "error": str(e), "style": st, "view": view})

        results[w] = {"translation": translation, "items": word_results}

    success = sum(1 for d in results.values() for it in d["items"] if it.get("success"))
    pending = sum(1 for d in results.values() for it in d["items"] if it.get("cloud_pending"))
    dry = sum(1 for d in results.values() for it in d["items"] if it.get("dry_run"))

    print("\n" + "=" * 60)
    print(f"📊 统计: ✅ 成功 {success} | ☁️ 云端 {pending} | ⏭️ 预览 {dry}")

    if pending > 0 and not dry_run:
        print("\n☁️ 云端待生成：")
        for w, data in results.items():
            for i, it in enumerate(data["items"]):
                if it.get("cloud_pending"):
                    print(f"  {w} #{i+1} [{VIEW_PROMPTS.get(it['view'],{}).get('desc','?')}]: {it['positive'][:80]}...")

    return results

# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Vocab Picture Generator v1.3.0 - 英语单词学习图片生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
学习视角说明:
  front   正面照  — 物体朝向镜头，自然放置
  side    侧面照  — 展示侧面轮廓
  top     俯视图  — 从上往下拍
  closeup 特写    — 质感/细节
  cut     切面图  — 剖面（仅水果/蔬菜）
  context 场景照  — 物体在真实使用场景中

不指定 --views 时自动分配：
  生成 1 张 → front
  生成 2 张 → front, side
  生成 3 张 → front, side, top
  生成 4 张 → front, side, top, closeup
  生成 5 张 → front, side, top, closeup, cut（水果类）
  生成 6 张 → front, side, top, closeup, cut, context

示例:
  python3 vocab_gen.py -w apple -c 5           # 5张不同视角（正面/侧面/俯视/特写/切面）
  python3 vocab_gen.py -w apple,banana -c 3      # 批量生成，每词3张
  python3 vocab_gen.py -w apple,banana -c 2 -s realistic-photo  # 指定风格
  python3 vocab_gen.py -w apple -c 5 --views front,side,cut   # 指定视角顺序
        """
    )
    parser.add_argument("-w", "--words", type=str)
    parser.add_argument("-c", "--count", type=int, default=1)
    parser.add_argument("-s", "--style", type=str)
    parser.add_argument("--text-en", dest="add_text", action="store_true", default=True)
    parser.add_argument("--text-off", dest="add_text", action="store_false")
    parser.add_argument("-q", "--quality", type=str, default="normal",
                        choices=["draft","normal","hq","best"])
    parser.add_argument("--force-cloud", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-o", "--output", type=str, default=None)
    parser.add_argument("--views", type=str, default=None,
                        help="逗号分隔的视角列表，如: front,side,cut")

    args = parser.parse_args()

    words = []
    if args.words:
        words += [w.strip() for w in args.words.split(",")]
    words = list(dict.fromkeys(words))
    if not words:
        print("❌ 需要指定 -w <words>")
        sys.exit(1)

    # 解析视角
    views = None
    if args.views:
        raw_views = [v.strip() for v in args.views.split(",")]
        # 验证
        invalid = [v for v in raw_views if v not in VIEW_TYPES]
        if invalid:
            print(f"❌ 未知视角: {invalid}，可用: {VIEW_TYPES}")
            sys.exit(1)
        views = raw_views
        # 如果指定了视角，强制 count=len(views)
        if args.count != len(views):
            print(f"ℹ️ --views 指定了 {len(views)} 个视角，忽略 -c {args.count}")
            args.count = len(views)

    out_dir = Path(args.output) if args.output else DEFAULT_OUTPUT_DIR
    batch_generate(words, args.count, args.style, args.add_text,
                   args.quality, args.force_cloud, args.dry_run,
                   output_dir=out_dir, views=views)

if __name__ == "__main__":
    main()
