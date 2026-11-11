"""Small presentation widgets; no SSH or disk operations."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QToolButton, QScrollArea, QTabWidget


class SectionStack(QTabWidget):
    """Keep page indices stable while providing scrollable sidebar navigation."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabBar().hide()

    def addTab(self, widget, label):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return super().addTab(scroll, label)


class ProcedureNotice(QFrame):
    def __init__(self, title, description, steps="", parent=None):
        super().__init__(parent)
        self.setObjectName("notice")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.title = QLabel(title)
        self.title.setObjectName("stepTitle")
        self.description = QLabel(description)
        self.description.setWordWrap(True)
        for label in (self.title, self.description):
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
        if steps:
            label = QLabel(steps)
            label.setObjectName("steps")
            label.setWordWrap(True)
            layout.addWidget(label)


class DetailsPanel(QFrame):
    def __init__(self, title, content, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        button = QToolButton()
        button.setText(title)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setArrowType(Qt.ArrowType.RightArrow)
        def toggle(expanded):
            content.setVisible(expanded)
            button.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
            self.setMaximumHeight(16777215 if expanded else button.sizeHint().height())
        button.toggled.connect(toggle)
        layout.addWidget(button)
        layout.addWidget(content, 1)
        content.hide()
        toggle(False)
        self.toggle = button
