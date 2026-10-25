"""Bandeau d'aide bleu en haut de la carte, commun aux outils de dessin : etapes de
l'outil (faites cochees, en cours soulignee), consigne de l'etape, touches utiles.
Menu Modes de dessin → Bandeau d'aide pour le couper (QSettings Box_cad/guide)."""
from qgis.core import Qgis
from qgis.gui import QgsMessageBarItem
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QLabel, QSizePolicy

GUIDE_KEY = "Box_cad/guide"


def guide_html(name, steps, index, hint, keys):
    """Trois lignes : etapes, consigne, touches."""
    line = " › ".join(
        f"<b><u>{step}</u></b>" if i == index else (f"✓ {step}" if i < index else step)
        for i, step in enumerate(steps))
    return f"<b>{name}</b> : {line}<br>{hint}<br><i>{keys}</i>"


class GuideBanner:
    """Un bandeau par activation d'outil. Lignes coupees a la main : avec le retour a la
    ligne automatique, la barre de messages garde la hauteur calculee sur la largeur
    initiale. Largeur minimale 1 px : le bandeau ne doit jamais elargir la fenetre QGIS.
    Ferme par sa croix, il ne revient qu'a la prochaine activation de l'outil."""

    def __init__(self, iface):
        self.iface = iface
        self.item = self.label = None
        self.closed = False

    def show(self, html):
        if self.closed:
            return
        bar = self.iface.messageBar()
        if self.item is not None and (sip.isdeleted(self.item) or self.item not in bar.items()):
            self.item = self.label = None       # ferme (croix, « Tout fermer »)
            self.closed = True
            return
        if self.item is None:
            self.label = QLabel()
            self.label.setTextFormat(Qt.TextFormat.RichText)
            self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.label.setMinimumWidth(1)
            self.item = QgsMessageBarItem(self.label, Qgis.MessageLevel.Info, 0)
            bar.pushItem(self.item)
        self.label.setText(html)

    def hide(self):
        item, self.item, self.label = self.item, None, None
        bar = self.iface.messageBar()
        if item is not None and not sip.isdeleted(item) and item in bar.items():
            bar.popWidget(item)
