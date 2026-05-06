---
name: vocab-picture
description: >-
  英语单词学习图片生成器。批量生成、学习视角系统（多角度/切面/场景）、本地优先、8种风格。
  触发词：单词图片、学单词、英语图片、vocab
version: 1.5.0
---

# Vocab Picture Skill v1.2

英语单词学习图片批量生成器，支持多种风格和本地/云端双模式。

## 核心功能

| 功能 | 说明 |
|------|------|
| **批量生成** | 单词变体批量 + 多单词批量 |
| **本地优先** | 自动启动 ComfyUI + VRAM 管理，省云端额度 |
| **8 种风格** | 扁平/卡通/真实照片/水彩/绘本/线条/复古/可爱 |
| **文字开关** | PIL 叠加英文+中文，避开 SD 文字生成弱点 |
| **模型匹配** | 风格自动匹配最佳模型（FLUX/RealVisXL/SD3） |

---

## 使用方式

### 命令行（推荐）

```bash
cd ~/.openclaw/workspace-cortana-shadow/skills/vocab-picture

# 基本用法
python3 vocab_gen.py -w apple,banana -c 2

# 指定风格
python3 vocab_gen.py -w cat -s realistic-photo

# 关闭文字叠加
python3 vocab_gen.py -w cat -s cartoon --text-off

# 主题批量（动物 10 词 × 2 张）
python3 vocab_gen.py -t animal -c 2

# 高质量
python3 vocab_gen.py -w apple,banana -q hq

# 强制云端
python3 vocab_gen.py -w apple --force-cloud

# 仅预览提示词（不生成）
python3 vocab_gen.py -w apple -s realistic-photo --dry-run
```

### Python API

```python
import sys
sys.path.insert(0, '~/.openclaw/workspace-cortana-shadow/skills/vocab-picture')
from vocab_gen import batch_generate, generate_vocab_image

# 批量生成
results = batch_generate(
    words=["apple", "banana", "cat"],
    count_per_word=2,
    style="cartoon",       # None=轮换风格
    add_text=True,
    quality="normal",
)
```

---

## 风格选项

| 风格 | 说明 | 最佳模型 |
|------|------|---------|
| `flat-illustration` | 扁平简约插画 | FLUX schnell |
| `cartoon` | 可爱卡通 | FLUX schnell |
| `realistic-photo` | 真实照片 | RealVisXL |
| `watercolor` | 水彩插画 | FLUX dev |
| `children-book` | 儿童绘本 | FLUX dev |
| `line-art` | 简约线条画 | FLUX schnell |
| `vintage` | 复古插画 | SD3 Medium |
| `kawaii` | 日系可爱 | FLUX dev |

**默认行为**：不指定风格时自动轮换 8 种风格（每词多张时风格不同）

---

## 参数说明

| 参数 | 说明 |
|------|------|
| `-w, --words` | 单词列表，逗号分隔 |
| `-c, --count` | 每词生成张数（变体数） |
| `-t, --theme` | 主题：`animal`, `food`, `fruit`, `vegetable`, `color`, `number`, `object`, `body`, `clothing`, `nature` |
| `-s, --style` | 风格：`flat-illustration`, `cartoon`, `realistic-photo`, `watercolor`, `children-book`, `line-art`, `vintage`, `kawaii` |
| `--text-en` | 叠加英文+中文文字（默认） |
| `--text-off` | 不叠加文字 |
| `-q, --quality` | 质量：`draft`(4步) / `normal`(8步) / `hq`(20步) / `best`(30步) |
| `--force-cloud` | 强制使用云端 |
| `--dry-run` | 仅预览提示词 |

---

## 模型选择策略

```
本地优先（自动）：
  1. ComfyUI 检查/启动 → VRAM 抢占 → 模型加载 → 生成 → VRAM 恢复
  2. 显存充足（≥20GB）→ RealVisXL（真实照片）/ FLUX dev（插画）
  3. 显存有限（≥10GB）→ FLUX schnell（快速生成）

风格 → 模型映射：
  realistic-photo → RealVisXL V4
  vintage         → SD3 Medium
  其他风格        → FLUX1 schnell / FLUX1 dev
```

---

## 内置词库

- **动物** animal: cat, dog, fish, bird, rabbit, elephant, lion, monkey, bear, panda
- **食物** food: apple, banana, orange, grape, strawberry, pizza, burger, cake, rice, bread
- **水果** fruit: apple, banana, orange, grape, strawberry, watermelon, mango, peach, pear, cherry
- **蔬菜** vegetable: carrot, tomato, potato, broccoli, corn, cucumber, lettuce, pepper, onion, garlic
- **颜色** color: red, blue, green, yellow, pink, purple, black, white, brown
- **数字** number: one, two, three, four, five, six, seven, eight, nine, ten
- **用品** object: book, pen, pencil, chair, table, bed, cup, ball, car, tree
- **身体** body: eye, nose, mouth, ear, hand, foot, head, arm, leg, finger
- **服装** clothing: hat, shirt, pants, dress, shoe, sock, coat, glove, scarf, boot
- **自然** nature: sun, moon, star, cloud, rain, snow, flower, grass, leaf, river

**总计 100+ 常用单词**，带中文翻译和视觉特征描述

---

## 输出

- **默认输出目录**：`~/Pictures/vocab/`
- **本地生成**：`~/ComfyUI/output/vocab_*.png` → 自动复制到 `~/Pictures/vocab/`
- **云端生成**：`~/.openclaw/media/tool-image-generation/`
- **文字叠加**：本地生成后自动叠加，底部白色条 + 英文在上中文在下 + NotoSansCJK 字体消除乱码
- **原始文件**：每张图同时保存 `_raw.png` 无文字版本

---

## 维护日志

- v1.5.0 (2026-05-06): **两步指示图**：真实照片（RealVisXL）+ PIL 画卡通手指后期合成；修复 vram_manager 卡死问题；QA 审核宽容处理合成图
- v1.4.0 (2026-05-06): **部位指示系统**：INDICATOR_VISUALS 词库（body/vehicle/animal），QA 审核（qwen2.5vl），不合格自动重生成
- v1.3.0 (2026-05-06): **学习视角系统**：多角度图片，水果蔬菜切面视角
- v1.2.2 (2026-05-06): 修复文字覆盖画面、底部白色条布局、NotoSansCJK 消除乱码、默认输出目录改为 ~/Pictures/vocab/
- v1.2.1 (2026-05-06): 修复 FLUX/SD3/RealVisXL 工作流
- v1.2.0 (2026-05-06): 新增 8 种风格、英语文字开关、PIL 叠加文字、本地 VRAM 管理
- v1.1.0 (2026-05-06): 新增批量生成、本地优先、提示词自动优化
- v1.0.0 (2026-05-06): 初始版本
