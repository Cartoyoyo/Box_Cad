"""Saisie dynamique facon DAO : invite + champs qui suivent le curseur.

Le widget ne prend jamais le focus : l'outil de carte lui transmet les touches.
Chaque champ a trois etats :
  - direct : affiche la valeur mesuree a la souris ;
  - saisie : l'utilisateur tape (curseur | affiche) ;
  - verrouille : valeur imposee (cadenas), la souris ne donne plus que le reste.

Pres du curseur : les valeurs seules. L'invite, le nom des champs (champ actif en gras)
et l'information (accrochage, surface...) vont dans la barre d'etat via `status`.
"""
import re
from html import escape

from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

STYLE = """
QFrame#boxcadDyn { background: rgba(255,255,255,235); border: 1px solid #9aa3ad; border-radius: 3px; }
QLabel[state="live"]   { background: #f3f4f6; color: #6b7280; border: 1px solid #d1d5db; }
QLabel[state="active"] { background: #ffffff; color: #111827; border: 1px solid #ea580c; }
QLabel[state="typing"] { background: #fff7ed; color: #111827; border: 1px solid #ea580c; font-weight: bold; }
QLabel[state="locked"] { background: #ffedd5; color: #9a3412; border: 1px solid #ea580c; font-weight: bold; }
QLabel[class="field"] { padding: 1px 5px; border-radius: 3px; font-size: 11px; min-width: 52px; }
"""

OFFSET = QPoint(20, 20)
HINT = re.compile(r"^(.*?)\s*\((.*)\)\s*:?\s*$")   # "Etape (touches) :"


class DynamicInput(QFrame):
    def __init__(self, canvas, status=None):
        super().__init__(canvas)
        self._status = status   # fonction(html) : texte de la barre d'etat
        self.setObjectName("boxcadDyn")
        self.setStyleSheet(STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._prompt = ""
        self._info = ""
        self._row = QHBoxLayout()
        self._row.setSpacing(3)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 2, 3, 2)
        lay.addLayout(self._row)
        self._fields = []       # [{key, label, unit, widget, live, typed, locked}]
        self._active = 0
        self.hide()

    # ------------------------------------------------------------------
    # Configuration par etape d'outil
    # ------------------------------------------------------------------

    def configure(self, prompt, fields):
        """fields : liste de (cle, libelle, unite). Remet les verrous a zero."""
        self._prompt = prompt
        self._info = ""         # l'information de l'etape precedente ne vaut plus
        for f in self._fields:
            f["widget"].deleteLater()
        self._fields = []
        for key, label, unit in fields:
            w = QLabel(self)
            w.setProperty("class", "field")
            self._row.addWidget(w)
            w.show()   # enfant ajoute apres l'affichage du cadre : invisible sinon
            self._fields.append({"key": key, "label": label, "unit": unit, "widget": w,
                                 "live": None, "typed": None, "locked": None})
        self._active = 0
        self._refresh()
        self.adjustSize()

    def set_live(self, values):
        """Valeurs mesurees a la souris, affichees dans les champs non verrouilles."""
        for f in self._fields:
            if f["key"] in values:
                f["live"] = values[f["key"]]
        self._refresh()

    def set_label(self, key, label):
        for f in self._fields:
            if f["key"] == key:
                f["label"] = label
        self._refresh()

    def set_info(self, text):
        """Ligne d'information sous les champs (surface, longueur totale...)."""
        if (text or "") != self._info:
            self._info = text or ""
            self._publish()

    def locked(self, key):
        """Valeur imposee : verrouillee, ou en cours de frappe si elle est deja lisible
        (l'apercu suit la saisie, comme en DAO)."""
        for f in self._fields:
            if f["key"] == key:
                if f["locked"] is not None:
                    return f["locked"]
                if f["typed"] not in (None, "", "-", "."):
                    try:
                        return float(f["typed"])
                    except ValueError:
                        return None
        return None

    def has_input(self):
        return any(f["typed"] is not None or f["locked"] is not None for f in self._fields)

    def is_typing(self):
        return bool(self._fields) and self._fields[self._active]["typed"] is not None

    # ------------------------------------------------------------------
    # Clavier (appele par l'outil)
    # ------------------------------------------------------------------

    def type_char(self, ch):
        if not self._fields:
            return False
        f = self._fields[self._active]
        text = f["typed"] or ""
        ch = "." if ch == "," else ch
        if ch == "." and "." in text:
            return True
        if ch == "-" and text:
            return True
        f["typed"] = text + ch
        self._refresh()
        return True

    def backspace(self):
        f = self._fields[self._active]
        if f["typed"]:
            f["typed"] = f["typed"][:-1] or None
            self._refresh()
            return True
        return False

    def commit_typing(self):
        """Verrouille la valeur en cours de saisie (si valide)."""
        if not self._fields:
            return
        f = self._fields[self._active]
        if f["typed"] is not None:
            try:
                f["locked"] = float(f["typed"])
            except ValueError:
                pass
            f["typed"] = None
        self._refresh()

    def next_field(self):
        self.commit_typing()
        if self._fields:
            self._active = (self._active + 1) % len(self._fields)
        self._refresh()

    def clear_input(self):
        for f in self._fields:
            f["typed"] = None
            f["locked"] = None
        self._active = 0
        self._refresh()

    # ------------------------------------------------------------------
    # Affichage
    # ------------------------------------------------------------------

    def follow(self, pos):
        """Place le cadre pres du curseur, sans sortir du canevas (rien a montrer sans champ)."""
        if not self._fields:
            self.hide()
            return
        canvas = self.parentWidget()
        self.adjustSize()
        x = min(pos.x() + OFFSET.x(), canvas.width() - self.width() - 4)
        y = min(pos.y() + OFFSET.y(), canvas.height() - self.height() - 4)
        if x < pos.x() < x + self.width() and y < pos.y() < y + self.height():
            y = pos.y() - self.height() - OFFSET.y()   # ne pas masquer le curseur
        self.move(max(x, 4), max(y, 4))
        if not self.isVisible():
            self.show()
        self.raise_()

    def _refresh(self):
        for i, f in enumerate(self._fields):
            if f["typed"] is not None:
                state, value = "typing", f["typed"] + "|"
            elif f["locked"] is not None:
                state, value = "locked", "🔒 " + _fmt(f["locked"])
            else:
                state = "active" if i == self._active else "live"
                value = _fmt(f["live"]) if f["live"] is not None else "…"
            w = f["widget"]
            w.setText(f"{value} {f['unit']}".rstrip())
            if w.property("state") != state:
                w.setProperty("state", state)
                w.style().unpolish(w)
                w.style().polish(w)
        self.adjustSize()
        self._publish()

    def _publish(self):
        """Invite + noms des champs (actif en gras) + information, dans la barre d'etat."""
        if self._status is None:
            return
        names = []
        for i, f in enumerate(self._fields):
            name = escape(f["label"], quote=False)
            names.append(f"<b>{name}</b>" if i == self._active else name)
        # l'aide entre parentheses passe en dernier : si la barre est etroite, c'est elle
        # qui est coupee, pas les champs
        match = HINT.match(self._prompt)
        step, hint = (match.group(1), match.group(2)) if match else (self._prompt.rstrip(" :"), "")
        html = "<b>Box_cad</b> · " + escape(step, quote=False)
        if names:
            html += " : " + " | ".join(names)
        if self._info:
            html += f" &nbsp;<span style='color:#9a3412'>{escape(self._info, quote=False)}</span>"
        if hint:
            html += f" &nbsp;<span style='color:#6b7280'>{escape(hint, quote=False)}</span>"
        self._status(html)


def _fmt(v):
    return f"{v:.2f}".replace(".", ",")
