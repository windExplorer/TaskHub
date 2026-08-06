"""适配层：把真实后端（CosyVoice / ComfyUI）接到中转站标准接口上。"""
from scheduler import BackendError
from .tts import TTSAdapter
from .comfy import ComfyAdapter

__all__ = ['TTSAdapter', 'ComfyAdapter', 'BackendError']
