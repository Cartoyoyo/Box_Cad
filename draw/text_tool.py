"""TEXTE : point d'insertion -> angle (Entree : 0°) -> fenetre de saisie (texte, taille).

Taille en points, fixe a l'ecran (le texte ne grossit pas avec le zoom). Les textes
vont dans le brouillon « Box_cad dessin – textes » (champs texte, taille, angle).
"""
from qgis.core import QgsGeometry, QgsPointXY
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QPlainTextEdit, QVBoxLayout,
)

from . import geometry as G
from .draw_tool_base import DrawTool

SIZE_KEY = "Box_cad/text_size"


def default_size():
    try:
        return float(QSettings().value(SIZE_KEY, 10))
    except (TypeError, ValueError):
        return 10.0


class TextDialog(QDialog):
    def __init__(self, parent=None, text="", size=10.0, title="Texte"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.edit = QPlainTextEdit(text)
        self.edit.setFixedHeight(70)
        self.size = QDoubleSpinBox()
        self.size.setRange(4, 96)
        self.size.setDecimals(1)
        self.size.setSuffix(" pt")
        self.size.setValue(size)
        form = QFormLayout()
        form.addRow("Texte :", self.edit)
        form.addRow("Taille :", self.size)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.edit.setFocus()

    def values(self):
        return self.edit.toPlainText().strip(), self.size.value()


class TextTool(DrawTool):
    TOOL_NAME = "texte"

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.text_angle = 0.0

    def start(self):
        self.points = []
        self.text_angle = 0.0
        self.configure("TEXTE · point d'insertion :")

    def compute(self, mouse):
        if not self.points:
            return mouse, None, None, {}, ""
        p = self.points[0]
        _, ang, _ = self.constrain_from(p, mouse, length_key="", angle_key="angle")
        self.text_angle = ang
        tip = G.polar_point(p, self.canvas().mapUnitsPerPixel() * 60, ang)
        return mouse, [p, tip], "line", {"angle": ang}, ""

    def on_click(self, pt):
        if not self.points:
            self.points = [QgsPointXY(pt)]
            self.configure("Angle du texte (Entrée : 0°) :", [("angle", "Angle", "°")])
            return
        self._place(self.text_angle)

    def on_finish(self, cancel=False):
        if not cancel and self.points:
            locked = self.dyn.locked("angle")
            self._place(locked % 360.0 if locked is not None else 0.0)
            return
        self.start()

    def prompt_text(self, size):
        """Fenetre de saisie ; retourne (texte, taille) ou None. Remplacable dans les tests."""
        dialog = TextDialog(self.canvas().window(), size=size)
        if dialog.exec():
            return dialog.values()
        return None

    def _place(self, angle):
        point = self.points[0]
        answer = self.prompt_text(default_size())
        if answer and answer[0]:
            text, size = answer
            QSettings().setValue(SIZE_KEY, size)
            self.commit(QgsGeometry.fromPointXY(point), "text",
                        {"texte": text, "taille": size, "angle": round(angle, 2)})
        self.start()
