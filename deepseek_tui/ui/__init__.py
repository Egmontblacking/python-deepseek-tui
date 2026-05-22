"""终端界面 — Textual 多面板布局（需 pip install textual）"""
try:
    from deepseek_tui.ui.app import DeepSeekTUI, run_textual
except ImportError:
    DeepSeekTUI = None
    run_textual = None
