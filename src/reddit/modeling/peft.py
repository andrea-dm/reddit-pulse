"""Quantization and PEFT (QDoRA / xQDoRA) configurations."""

import torch
from peft import LoraConfig
from transformers import BitsAndBytesConfig

quantization_config = BitsAndBytesConfig(
    load_in_4bit=True,  # enable 4-bit quantization
    bnb_4bit_quant_type="nf4",  # information-theoretically optimal for normal weights
    bnb_4bit_use_double_quant=True,  # quantize the quantized weights
    bnb_4bit_compute_dtype=torch.float16,
)

target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]

peft_config = {
    "qdora": LoraConfig(  # QDoRA+
        task_type="SEQ_CLS",
        target_modules=target_modules,
        r=32,
        lora_alpha=32,
        lora_dropout=0.1,
        use_dora=True,
    ),
    "xqdora": LoraConfig(  # xQDoRA+
        task_type="SEQ_CLS",
        target_modules=target_modules,
        r=4,
        lora_alpha=32,
        lora_dropout=0.05,
        use_dora=True,
    ),
}
