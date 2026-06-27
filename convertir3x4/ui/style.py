"""Hoja de estilos (tema oscuro) de la app."""

DARK_STYLE = """
QMainWindow, QWidget { background-color: #1a1d23; color: #e0e0e0; }
QPushButton {
    background-color: #2a3040; color: #e0e0e0; border: 1px solid #3a4558;
    border-radius: 6px; padding: 6px 14px; font-size: 13px;
}
QPushButton:hover { background-color: #354060; border-color: #4a5a78; }
QPushButton:pressed { background-color: #0078d4; }
QPushButton:disabled { color: #555555; background-color: #1e2230; border-color: #252a35; }
QPushButton[active="true"] { border-color: #0078d4; color: #5aafff; }
QPushButton#btn_icon {
    background-color: transparent; border: 1px solid #2a3040; border-radius: 4px;
    font-size: 16px; padding: 0px;
}
QPushButton#btn_icon:hover { background-color: #354060; border-color: #4a5a78; }
QPushButton#btn_icon:disabled { opacity: 0.3; background-color: transparent; border-color: #252a35; }
QPushButton#btn_load { background-color: #1a3550; border-color: #2a5070; color: #70b8ff; }
QPushButton#btn_load:hover { background-color: #204060; border-color: #3070a0; }
QPushButton#btn_action { background-color: #1a3a2a; border-color: #2a5a3a; color: #70d090; }
QPushButton#btn_action:hover { background-color: #204a34; border-color: #30704a; }
QPushButton#btn_action:disabled { color: #445544; background-color: #1e2520; border-color: #252a35; }
QPushButton#btn_load_bg {
    background-color: transparent; border: none; color: #6680a0;
    font-size: 12px; padding: 4px 8px;
}
QPushButton#btn_load_bg:hover { color: #88aacc; }
QFrame#sidebar {
    background-color: #1e2230; border: 1px solid #2a3040; border-radius: 6px;
}
QFrame#separator { background-color: #2a3040; }
QLabel#group_title { color: #556070; font-size: 11px; font-weight: bold; letter-spacing: 2px; }
QLabel#section_title { color: #7090b0; font-size: 12px; font-weight: bold; }
QLabel#bg_preview {
    background-color: #252a38; border: 1px solid #3a4558; border-radius: 4px;
}
QListWidget {
    background-color: #1e2230; border: 1px solid #2a3040; border-radius: 6px;
    outline: none;
}
QListWidget::item {
    background-color: #252a38; border: 2px solid transparent;
    border-radius: 4px; padding: 4px; margin: 4px;
}
QListWidget::item:selected { border-color: #0078d4; background-color: #1a3050; }
QListWidget::item:hover:!selected { background-color: #2a3248; }
QProgressBar {
    background-color: #252a38; border: none; border-radius: 3px; height: 6px;
}
QProgressBar::chunk { background-color: #0078d4; border-radius: 3px; }
QLabel { color: #8899aa; font-size: 13px; }
QSlider::groove:horizontal {
    background: #2a3040; height: 4px; border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #0078d4; width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: #3098e8; }
QPushButton#btn_sort {
    background-color: transparent; border: none; color: #6680a0;
    font-size: 12px; padding: 4px 8px;
}
QPushButton#btn_sort:hover { color: #88aacc; }
QPushButton#btn_sort[active="true"] { color: #5aafff; }
QComboBox {
    background-color: #252a38; color: #e0e0e0; border: 1px solid #3a4558;
    border-radius: 4px; padding: 4px 8px; font-size: 12px;
}
QComboBox:hover { border-color: #4a5a78; }
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #252a38; color: #e0e0e0; border: 1px solid #3a4558;
    selection-background-color: #1a3050;
}
QCheckBox { color: #8899aa; font-size: 12px; spacing: 6px; }
QCheckBox::indicator {
    width: 14px; height: 14px; border: 1px solid #3a4558;
    border-radius: 3px; background-color: #252a38;
}
QCheckBox::indicator:checked { background-color: #0078d4; border-color: #0078d4; }
QCheckBox::indicator:hover { border-color: #4a5a78; }
QPushButton#tag_chip {
    background-color: #1a3050; border: 1px solid #2a5070; color: #70b8ff;
    border-radius: 11px; padding: 5px 12px; font-size: 12px;
}
QPushButton#tag_chip:hover { background-color: #24406a; border-color: #3070a0; }
QPushButton#tag_chip_partial {
    background-color: #2a2a1a; border: 1px solid #50502a; color: #c0b060;
    border-radius: 11px; padding: 5px 12px; font-size: 12px;
}
QPushButton#tag_chip_partial:hover { background-color: #3a3a24; border-color: #707030; }
QTabWidget::pane { border: 1px solid #2a3040; border-radius: 6px; top: -1px; }
QTabBar::tab {
    background-color: #1e2230; color: #8899aa; border: 1px solid #2a3040;
    border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px;
    padding: 6px 16px; margin-right: 2px;
}
QTabBar::tab:selected { background-color: #2a3040; color: #5aafff; }
QTabBar::tab:hover:!selected { background-color: #252a38; }
"""
