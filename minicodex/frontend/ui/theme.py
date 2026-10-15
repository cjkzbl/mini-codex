STYLESHEET = """
QWidget { background: #191a1d; color: #e3e8ef; font-family: 'Segoe UI', 'Microsoft YaHei UI';
          font-size: 13px; }
QMainWindow { background: #191a1d; }
QFrame#composer { background: #25272c; border: 1px solid #42464e; border-radius: 14px; }
QPlainTextEdit#prompt { background: transparent; border: none; padding: 8px; }
QComboBox#permissionPicker { background: #25272c; border: 1px solid #42464e;
                             border-radius: 6px; padding: 6px 10px; min-width: 96px; }
QPushButton#modelPicker { background: transparent; border: none; padding: 6px 8px;
                         color: #b9c3cf; font-weight: 400; }
QPushButton#modelPicker:hover { background: #34373e; }
QFrame#panel { background: #202125; border: 1px solid #303238; border-radius: 12px; }
QFrame#sidebar { background: #191a1d; border: none; }
QScrollArea#conversation, QWidget#conversationContent { background: #202125; }
QFrame#userBubble { background: #2d3036; border: 1px solid #393d45; border-radius: 12px; }
QFrame#stepCard { background: #24262b; border: 1px solid #34373e; border-radius: 10px; }
QFrame#toolCard { background: #1e2024; border: 1px solid #363a42; border-radius: 7px; }
QFrame#approvalCard { background: #23342e; border: 1px solid #476454; border-radius: 10px; }
QLabel#speaker { color: #e5e8ed; font-size: 12px; font-weight: 600; }
QLabel#stepTitle { color: #a5b1c2; font-size: 12px; }
QLabel#assistantText, QLabel#userText { color: #e5e8ed; font-size: 14px; }
QLabel#turnSummary { color: #a2b5a9; font-size: 12px; padding-top: 6px; }
QLabel#welcome { color: #9ba9bb; font-size: 16px; }
QToolButton#toolToggle { background: transparent; border: none; color: #c8d2e0;
                         text-align: left; padding: 4px; }
QToolButton#toolToggle:hover { color: #a2e9c2; }
QToolButton#toolToggle[outcome="success"] { color: #a2cfb4; }
QToolButton#toolToggle[outcome="failure"] { color: #f0a6a6; }
QPlainTextEdit#toolDetails { font-family: 'Cascadia Mono', 'Consolas', 'Microsoft YaHei UI';
                            font-size: 12px; padding: 6px; }
QLabel { background: transparent; }
QLabel#brand { font-size: 25px; font-weight: 700; color: #f4f7fb; }
QLabel#section { font-size: 14px; font-weight: 600; color: #f4f7fb; }
QLabel#muted { color: #9ba9bb; }
QLabel#badge { background: #20372f; color: #a2e9c2; border-radius: 7px; padding: 6px 12px; }
QPushButton { background: #242e3c; border: 1px solid #39475a; border-radius: 7px;
              padding: 9px 14px; font-weight: 600; }
QPushButton:hover { background: #303d50; }
QPushButton:disabled { color: #637185; border-color: #283141; background: #1d2531; }
QPushButton#primary { background: #4d81e9; color: white; border: none; }
QPushButton#primary:hover { background: #6394f2; }
QPushButton#primary:disabled { background: #294267; color: #8198b7; }
QPushButton#approve { background: #326649; color: white; border: none; }
QPushButton#approve:hover { background: #418257; }
QPushButton#approve:disabled { background: #2b3b31; color: #81978a; }
QTabWidget::pane { border: 1px solid #303c4e; border-radius: 8px; }
QTabBar::tab { background: #242e3c; color: #9ba9bb; padding: 8px; }
QTabBar::tab:selected { background: #303d50; color: #e3e8ef; }
QPlainTextEdit { background: #131923; border: 1px solid #303c4e; border-radius: 8px;
                 padding: 10px; selection-background-color: #31568f; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 0px; border-radius: 6px; }
QListWidget::item:selected { background: #24364f; color: #c8dcff; }
QWidget#sessionRow { background: transparent; }
QPushButton#sessionTitle { background: transparent; border: none; padding: 6px;
                          text-align: left; font-weight: 400; }
QToolButton#sessionMore { background: transparent; border: none; border-radius: 5px;
                         padding: 2px; font-size: 19px; }
QToolButton#sessionMore:hover { background: #354255; }
QToolButton#sessionMore::menu-indicator { image: none; width: 0px; }
QMenu { background: #272a30; border: 1px solid #42464e; padding: 5px; }
QMenu::item { padding: 7px 20px; border-radius: 4px; }
QMenu::item:selected { background: #354255; }
QSplitter::handle { background: #191a1d; width: 6px; }
QStatusBar { background: #191a1d; color: #9ba9bb; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0px; }
QScrollBar::handle:vertical { background: #484c55; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
