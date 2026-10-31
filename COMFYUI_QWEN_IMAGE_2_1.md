# Qwen Image 2.1 本地商品图编辑流程

```text
加载原商品图 ─┐
              ├─ TextEncodeQwenImage21 ── 正向/负向条件 + 图生图 latent ─┐
加载 CLIP ────┤                                                        ├─ KSampler ─ 解码 ─ 保存
加载 VAE ─────┘                                                        │
加载 UNet ─ QwenImage21Cache（CPU + int8）─────────────────────────────┘
```

工作台会将同一份 API 工作流提交到本机 `http://127.0.0.1:8188`。如需在 ComfyUI 中检查节点参数，请打开 `comfyui-qwen-image-2.1-edit-api.json` 作为 API 工作流参考；原图对应 `LoadImage`，编辑提示对应 `TextEncodeQwenImage21`。

需要下载并由 ComfyUI 识别的模型：

- `qwen_image_2.1_int8_convrot.safetensors`：`models/diffusion_models/`
- `qwen3vl_8b_int8_convrot.safetensors`：`models/text_encoders/`
- `qwen_image_2.1_vae_bf16.safetensors`：`models/vae/`

缓存节点默认使用 `cpu` 和 `int8`，以适配 16GB 显存的 RTX 4070 Ti SUPER。 
