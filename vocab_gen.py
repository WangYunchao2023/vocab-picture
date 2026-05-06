#!/usr/bin/env python3
"""
Vocab Picture Generator v1.3.0 - 英语单词学习图片批量生成器
版本: 1.3.0

核心改进（v1.3）：
- 学习视角系统：每个单词自动生成多角度图片
- 视角类型：正面/侧面/俯视/特写/切面/场景
- 学习友好提示词：简单清晰、符合实物教学需求
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
# 词库
# ═══════════════════════════════════════════════════════════════

WORD_THEMES = {
    "animal":    ["cat","dog","fish","bird","rabbit","elephant","lion","monkey","bear","panda"],
    "food":      ["apple","banana","orange","grape","strawberry","pizza","burger","cake","rice","bread"],
    "fruit":     ["apple","banana","orange","grape","strawberry","watermelon","mango","peach","pear","cherry"],
    "vegetable": ["carrot","tomato","potato","broccoli","corn","cucumber","lettuce","pepper","onion","garlic"],
    "color":     ["red","blue","green","yellow","pink","purple","orange","black","white","brown"],
    "number":    ["one","two","three","four","five","six","seven","eight","nine","ten"],
    "object":    ["book","pen","pencil","chair","table","bed","cup","ball","car","tree"],
    "body":      ["eye","nose","mouth","ear","hand","foot","head","arm","leg","finger"],
    "clothing":  ["hat","shirt","pants","dress","shoe","sock","coat","glove","scarf","boot"],
    "nature":    ["sun","moon","star","cloud","rain","snow","flower","grass","leaf","river"],
}

WORD_TRANSLATIONS = {
    "cat":"猫","dog":"狗","fish":"鱼","bird":"鸟","rabbit":"兔子","elephant":"大象",
    "lion":"狮子","monkey":"猴子","bear":"熊","panda":"熊猫",
    "apple":"苹果","banana":"香蕉","orange":"橙子","grape":"葡萄","strawberry":"草莓",
    "pizza":"披萨","burger":"汉堡","cake":"蛋糕","rice":"米饭","bread":"面包",
    "watermelon":"西瓜","mango":"芒果","peach":"桃子","pear":"梨","cherry":"樱桃",
    "carrot":"胡萝卜","tomato":"番茄","potato":"土豆","broccoli":"西兰花","corn":"玉米",
    "cucumber":"黄瓜","lettuce":"生菜","pepper":"辣椒","onion":"洋葱","garlic":"大蒜",
    "red":"红色","blue":"蓝色","green":"绿色","yellow":"黄色","pink":"粉色",
    "purple":"紫色","black":"黑色","white":"白色","brown":"棕色",
    "one":"一","two":"二","three":"三","four":"四","five":"五",
    "six":"六","seven":"七","eight":"八","nine":"九","ten":"十",
    "book":"书","pen":"钢笔","pencil":"铅笔","chair":"椅子","table":"桌子",
    "bed":"床","cup":"杯子","ball":"球","car":"汽车","tree":"树",
    "eye":"眼睛","nose":"鼻子","mouth":"嘴巴","ear":"耳朵","hand":"手",
    "foot":"脚","head":"头","arm":"手臂","leg":"腿","finger":"手指",
    "hat":"帽子","shirt":"衬衫","pants":"裤子","dress":"连衣裙","shoe":"鞋子",
    "sock":"袜子","coat":"外套","glove":"手套","scarf":"围巾","boot":"靴子",
    "sun":"太阳","moon":"月亮","star":"星星","cloud":"云","rain":"雨",
    "snow":"雪","flower":"花","grass":"草","leaf":"叶子","river":"河流",
}

# 视觉特征（核心描述）
WORD_VISUALS = {
    "cat":"orange tabby cat, fluffy fur, sitting pose",
    "dog":"brown puppy, floppy ears, happy tail wagging",
    "fish":"orange goldfish, translucent fins, swimming",
    "bird":"blue tit bird, small, perched on branch",
    "rabbit":"white bunny, long ears, cute hop pose",
    "elephant":"gray elephant, big ears, gentle expression",
    "lion":"golden mane lion, majestic, alert gaze",
    "monkey":"brown monkey, playful, natural pose",
    "bear":"brown bear, fluffy fur, standing on hind legs",
    "panda":"black and white giant panda, eating bamboo",
    "apple":"a fresh red apple, natural shape, small green leaf attached",
    "banana":"a ripe yellow banana, slightly curved, one end slightly green",
    "orange":"a fresh orange fruit, slightly dimpled skin, bright color",
    "grape":"a bunch of purple grapes, natural cluster, slight bloom on skin",
    "strawberry":"a fresh red strawberry, seeds visible, green cap with stem",
    "watermelon":"a slice of watermelon, red flesh, black seeds scattered",
    "mango":"a ripe yellow mango, natural oval shape, slight blush on skin",
    "peach":"a pink-blushed peach, fuzzy skin, natural crease",
    "pear":"a green-yellow pear, teardrop shape, natural stem",
    "cherry":"two red cherries, connected by stems, fresh and glossy",
    "carrot":"an orange carrot, tapered root, fresh green tops attached",
    "tomato":"a bright red tomato, smooth skin, natural stem cap",
    "potato":"a brown oval potato, slightly dirty, natural eyes",
    "broccoli":"a fresh head of broccoli, dark green florets, thick stalk",
    "corn":"a yellow ear of corn, husks partially peeled, silk visible",
    "cucumber":"a long green cucumber, slightly curved, matte skin",
    "lettuce":"a fresh green lettuce head, loose leaves, crisp",
    "pepper":"a red bell pepper, glossy skin, three-lobed shape",
    "onion":"a brown onion, papery skin, round shape",
    "garlic":"a white garlic bulb, individual cloves visible, papery skin",
    "red":"a solid red color swatch, clean surface",
    "blue":"a solid blue color swatch, clean surface",
    "green":"a solid green color swatch, clean surface",
    "yellow":"a solid yellow color swatch, clean surface",
    "pink":"a solid pink color swatch, clean surface",
    "purple":"a solid purple color swatch, clean surface",
    "book":"an open hardcover book, colorful pages visible",
    "pen":"a blue ballpoint pen, sleek body, cap removed",
    "pencil":"a yellow Number 2 pencil, sharpened tip, pink eraser",
    "chair":"a wooden dining chair, simple design, four legs",
    "table":"a wooden dining table, four legs, natural wood grain",
    "bed":"a cozy bed with pillows, neatly made, warm comforter",
    "cup":"a white ceramic cup, handle on side, empty inside",
    "ball":"a red rubber ball, slightly deflated, smooth surface",
    "car":"a red toy car, small scale model, simple design",
    "tree":"a large green oak tree, round canopy, brown textured trunk",
    "eye":"a human eye, detailed iris, natural lashes, direct gaze",
    "nose":"a human nose, front view, natural shape",
    "mouth":"a smiling human mouth, natural teeth, relaxed expression",
    "ear":"a human ear, side view, natural shape and curves",
    "hand":"an open human palm, five fingers spread, natural pose",
    "foot":"a human foot, side view, natural arch, five toes",
    "head":"a human head, front view, neutral expression, natural",
    "arm":"a human arm, relaxed pose, natural skin tone",
    "leg":"a human leg, standing pose, natural proportions",
    "finger":"a human finger, index finger pointing, natural nail",
    "hat":"a red baseball cap, curved brim, fabric texture",
    "shirt":"a blue cotton shirt, folded, visible collar and buttons",
    "pants":"a pair of brown trousers, folded, waistband visible",
    "dress":"a pink summer dress, simple A-line silhouette, sleeveless",
    "shoe":"a white running shoe, side view, lace detail visible",
    "sock":"a white cotton sock, ankle length, ribbed cuff",
    "coat":"a yellow winter coat, hood visible, zipper partially open",
    "glove":"a red winter glove, five fingers, knit texture",
    "scarf":"a blue wool scarf, folded, soft texture visible",
    "boot":"a brown leather boot, ankle height, lace-up front",
    "sun":"the bright sun in blue sky, warm golden rays, morning light",
    "moon":"a crescent moon, white glow, dark night sky with stars",
    "star":"a bright star in night sky, twinkling, with smaller stars around",
    "cloud":"a fluffy white cloud, blue sky background, cotton-like shape",
    "rain":"raindrops falling, wet street surface, gray overcast sky",
    "snow":"snowflakes falling, white snow on ground, winter scene",
    "flower":"a pink rose bloom, petals open, green stem with thorns",
    "grass":"a patch of green grass, dew drops on blades, close-up",
    "leaf":"a green maple leaf, detailed veins, autumn leaf shape",
    "river":"a flowing river, clear blue water, rocks and pebbles on riverbed",
}

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

def build_workflow(prompt, negative, seed, steps, width, height, style_key):
    style = STYLES.get(style_key, STYLES["flat-illustration"])
    model_name = style["model_name"]
    mtype = style["type"]
    if mtype == "flux":
        return build_flux_workflow(prompt, negative, seed, steps, width, height, model_name, cfg=1.0)
    elif mtype == "sd3":
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

def comfy_generate(prompt, negative, seed, steps, width, height, style_key) -> str:
    from comfy_client import ComfyUIClient
    from vram_manager import VMgr
    style = STYLES.get(style_key, STYLES["flat-illustration"])
    model_key = style["model"]
    if model_key not in _WARMED_MODELS:
        comfy_warmup_model(model_key)
        _WARMED_MODELS.add(model_key)
    client = ComfyUIClient()
    vm = VMgr()
    print("   📦 抢占 VRAM...", end="", flush=True)
    acquired = vm.acquire_for_comfy(reason="vocab-picture")
    print(f" {'✅' if acquired else '❌'}")
    try:
        wf = build_workflow(prompt, negative, seed, steps, width, height, style_key)
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
        print("   🔄 释放 VRAM...", end="", flush=True)
        vm.release_and_restore()
        print(f" ✅")

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

# ═══════════════════════════════════════════════════════════════
# 单张生成
# ═══════════════════════════════════════════════════════════════

STEPS_MAP = {"draft": 4, "normal": 8, "hq": 20, "best": 30}

def generate(word, style_key, add_text, seed, quality, use_local,
             output_dir=None, view=None):
    word = word.lower().strip()
    translation = get_translation(word)
    style_key = style_key if style_key in STYLES else "flat-illustration"
    steps = STEPS_MAP.get(quality, 8)
    if seed is None:
        seed = random.randint(0, 2**32-1)
    positive, negative = build_prompt(word, style_key, view=view)
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
                    print(f"✅ {fname}")
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
  python3 vocab_gen.py -t fruit -c 3           # 水果主题，每词3张
  python3 vocab_gen.py -w apple,banana -c 2 -s realistic-photo  # 指定风格
  python3 vocab_gen.py -w apple -c 5 --views front,side,cut   # 指定视角顺序
        """
    )
    parser.add_argument("-w", "--words", type=str)
    parser.add_argument("-c", "--count", type=int, default=1)
    parser.add_argument("-t", "--theme", type=str)
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
    if args.theme:
        t = args.theme.lower()
        if t not in WORD_THEMES:
            print(f"❌ 未知主题: {t}")
            sys.exit(1)
        words = WORD_THEMES[t]
    if args.words:
        words += [w.strip() for w in args.words.split(",")]
    words = list(dict.fromkeys(words))
    if not words:
        print("❌ 需要指定 -w <words> 或 -t <theme>")
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
