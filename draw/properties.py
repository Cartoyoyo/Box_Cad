"""PROPRIETES des dessins Box_cad : couleur, epaisseur, type de trait, remplissage,
opacite (textes : contenu et taille). Entree sans objet : proprietes courantes,
appliquees aux prochaines formes. Vos couches (hors brouillons) ne sont jamais touchees.
"""
import json

from qgis.gui import QgsColorButton
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QPlainTextEdit, QSpinBox, QVBoxLayout,
)

from . import target as T
from .pick import draft_layers, pick_feature
from .transform import STEP_PICK, TransformTool

PROPS_KEY = "Box_cad/props"
LINE_STYLES = [("solid", "Continu"), ("dash", "Tirets"), ("dot", "Pointillés"), ("dash dot", "Mixte (axe)")]


def load_current_props():
    try:
        data = json.loads(QSettings().value(PROPS_KEY, "{}") or "{}")
    except (TypeError, ValueError):
        data = {}
    return {k: data.get(k) for k in T.PROPERTY_FIELDS}


def save_current_props(props):
    QSettings().setValue(PROPS_KEY, json.dumps({k: props.get(k) for k in T.PROPERTY_FIELDS}))


class PropertiesDialog(QDialog):
    """values : dict des champs ; kind : 'line' | 'polygon' | 'dimension' | 'text' | 'current'."""

    def __init__(self, parent, values, kind):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle("Propriétés courantes" if kind == "current" else "Propriétés de la forme")
        form = QFormLayout()

        self.color = QgsColorButton()
        self.color.setColor(QColor(values.get("couleur") or T.DEFAULT_INK))
        form.addRow("Couleur :", self.color)

        self.width = self.style = self.filled = self.opacity = self.text = self.size = None
        if kind != "text":
            self.width = QDoubleSpinBox()
            self.width.setRange(0.1, 5.0)
            self.width.setSingleStep(0.1)
            self.width.setDecimals(2)
            self.width.setSuffix(" mm")
            self.width.setValue(values.get("epaisseur") or T.DEFAULT_WIDTH)
            form.addRow("Épaisseur :", self.width)
            self.style = QComboBox()
            for key, label in LINE_STYLES:
                self.style.addItem(label, key)
            self.style.setCurrentIndex(max(0, self.style.findData(values.get("trait") or "solid")))
            form.addRow("Type de trait :", self.style)
        if kind in ("polygon", "current"):
            self.filled = QCheckBox("Rempli")
            self.filled.setChecked(bool(values.get("rempli")))
            form.addRow("Remplissage :", self.filled)
            self.opacity = QSpinBox()
            self.opacity.setRange(0, 100)
            self.opacity.setSuffix(" %")
            self.opacity.setValue(values.get("opacite") if values.get("opacite") is not None else 35)
            form.addRow("Opacité du remplissage :", self.opacity)
        if kind == "text":
            self.text = QPlainTextEdit(values.get("texte") or "")
            self.text.setFixedHeight(60)
            form.addRow("Texte :", self.text)
            self.size = QDoubleSpinBox()
            self.size.setRange(4, 96)
            self.size.setDecimals(1)
            self.size.setSuffix(" pt")
            self.size.setValue(values.get("taille") or 10)
            form.addRow("Taille :", self.size)

        self.make_current = None
        if kind != "current":
            self.make_current = QCheckBox("Utiliser pour les prochaines formes")
            form.addRow("", self.make_current)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self):
        out = {"couleur": self.color.color().name()}
        if self.width is not None:
            out["epaisseur"] = round(self.width.value(), 2)
            out["trait"] = self.style.currentData()
        if self.filled is not None:
            out["rempli"] = 1 if self.filled.isChecked() else 0
            out["opacite"] = self.opacity.value()
        if self.text is not None:
            out["texte"] = self.text.toPlainText().strip()
            out["taille"] = self.size.value()
        return out, bool(self.make_current and self.make_current.isChecked())


class PropertiesTool(TransformTool):
    """Clic sur une forme dessinee -> fenetre ; Entree sans objet -> proprietes courantes."""
    NAME = "Propriétés"

    def start(self):
        super().start()
        self.configure("PROPRIÉTÉS · cliquez une forme dessinée (Entrée : propriétés courantes) :")

    def compute(self, mouse):
        if self.step == STEP_PICK:
            self.hover = pick_feature(self.canvas(), draft_layers(), self.frame.to_map(mouse))   # dessins
        return mouse, None, None, {}, ""

    def wants_osnap(self):
        return False

    def prompt_properties(self, values, kind):
        """Fenetre de proprietes ; retourne (valeurs, devenir_courantes) ou None.
        Remplacable dans les tests."""
        dialog = PropertiesDialog(self.canvas().window(), values, kind)
        if dialog.exec():
            return dialog.values()
        return None

    def after_pick(self):
        layer, feature = self.target
        kind = layer.customProperty(T.DRAFT_PROPERTY)
        names = [f.name() for f in layer.fields()]
        current = {n: feature[n] for n in names}
        current = {k: (None if v is None or str(v) == "NULL" else v) for k, v in current.items()}
        answer = self.prompt_properties(current, kind)
        if answer:
            values, make_current = answer
            changes, old = {}, {}
            for name, value in values.items():
                idx = layer.fields().indexOf(name)
                if idx >= 0:
                    changes[idx] = value
                    old[idx] = feature.attribute(idx)
            if changes and layer.dataProvider().changeAttributeValues({feature.id(): changes}):
                self.plugin._set_undo(('draft_attrs', layer.id(), feature.id(), old))
                layer.triggerRepaint()
            if make_current:
                self._set_current(values)
        self.start()

    def on_finish(self, cancel=False):
        if not cancel and self.step == STEP_PICK:
            values = dict(self.plugin.current_props)
            values["rempli"] = 1 if self.plugin.draw_fill else 0
            answer = self.prompt_properties(values, "current")
            if answer:
                self._set_current(answer[0])
        self.start()

    def _set_current(self, values):
        self.plugin.set_current_props(values)
