"""Fenetre « A propos » facon CanaPlan : bandeau, badges, presentation justifiee,
encadre « A savoir », auteur + version, badges GitHub / LinkedIn, bouton Fermer.
Images dans icons/about (generees par ~/.local/bin/qgis_about_assets.py)."""
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

PLUGIN_DIR = os.path.dirname(__file__)
ASSETS = os.path.join(PLUGIN_DIR, "icons", "about").replace("\\", "/")
REPO = "https://github.com/Cartoyoyo/Box_Cad"
LINKEDIN = "https://www.linkedin.com/in/ylaloux/"

PRESENTATION = [
    "Box_cad ajoute à QGIS une barre de 12 outils qui agissent directement sur l'objet cliqué : "
    "retrouver sa couche, la masquer, masquer ou supprimer l'objet, ou changer l'ordre d'affichage "
    "de sa couche.",
    "Fini les allers-retours entre la carte et un panneau de couches de 40 lignes : cliquez sur ce "
    "qui vous gêne, Box_cad trouve la bonne couche et agit dessus. Chaque action se défait avec le "
    "bouton Annuler.",
]
NOTICE = ("<b>À savoir</b> — Les formes dessinées vont dans des couches brouillon en mémoire : "
          "exportez-les pour les conserver. Supprimer entité modifie vos couches ; une fois "
          "celles-ci enregistrées, la suppression ne peut plus être annulée.")
NOTICE_CSS = ("background:#FFF4E5; color:#6B4200; border:1px solid #E0A040;"
              "border-radius:4px; padding:8px;")


def _version():
    """Version lue dans metadata.txt (ligne a ligne : pas de configparser)."""
    path = os.path.join(PLUGIN_DIR, "metadata.txt")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "?"


def _badge(href, filename):
    return f'<a href="{href}"><img src="{ASSETS}/{filename}"></a>'


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("À propos Box_cad")
        self.setMinimumWidth(420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        center = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter

        # Bandeau : logo, nom, sous-titre
        banner = QLabel()
        banner.setPixmap(QPixmap(f"{ASSETS}/logo-full.png"))
        banner.setAlignment(center)
        layout.addWidget(banner)

        # Badges
        sep = "&nbsp;&nbsp;"
        badges = QLabel('<div style="line-height:220%">'
                        + _badge("https://qgis.org", "badge-qgis.png") + sep
                        + _badge(REPO, "badge-autocad.png") + sep
                        + _badge(f"{REPO}#readme", "badge-doc.png") + "</div>")
        badges.setAlignment(center)
        badges.setWordWrap(True)
        badges.setOpenExternalLinks(True)
        layout.addWidget(badges)

        layout.addWidget(self._separator())

        # Presentation, justifiee
        text = QLabel("<br><br>".join(PRESENTATION))
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignJustify)
        layout.addWidget(text)

        notice = QLabel(NOTICE)
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignmentFlag.AlignJustify)
        notice.setStyleSheet(NOTICE_CSS)
        layout.addWidget(notice)

        layout.addWidget(self._separator())

        # Auteur + version (lien vers le changelog), badges GitHub / LinkedIn
        author = QLabel(f'<b>Yoan Laloux</b><br><a href="{REPO}/blob/main/README.md#changelog">'
                        f'version {_version()}</a>')
        author.setAlignment(center)
        author.setOpenExternalLinks(True)
        layout.addWidget(author)

        links = QLabel(_badge(REPO, "badge-github.png") + sep + _badge(LINKEDIN, "badge-linkedin.png"))
        links.setAlignment(center)
        links.setOpenExternalLinks(True)
        layout.addWidget(links)

        layout.addWidget(self._separator())

        row = QHBoxLayout()
        row.addStretch()
        btn_close = QPushButton("Fermer")
        btn_close.setMinimumWidth(100)
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        row.addStretch()
        layout.addLayout(row)
        self.adjustSize()

    @staticmethod
    def _separator():
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        return sep
