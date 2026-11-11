"""JLC 制板门禁子包（移植自 jackwang988/jlceda-pcb-ai-workflow，MIT）。

对外的唯一入口是 ``jlc.api``；其余模块是内部实现，不保证跨版本稳定。
所有阈值都只存在于 ``jlc/data/*.json``（每条带 src/tier/url/fetched_at/quote），
本包内代码不写裸阈值。
"""

from . import api

__all__ = ["api"]
__version__ = "0.11.0-jlc"
