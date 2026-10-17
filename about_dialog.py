import os

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

PLUGIN_DIR = os.path.dirname(__file__)
LINKEDIN = "https://www.linkedin.com/in/ylaloux/"

# (icone, outil, description) dans l'ordre de la barre d'outils
TOOLS = [
    ("pick_layer.svg", "Localiser couche", "clic sur un objet : sa couche est sélectionnée dans le panneau"),
    ("delete_feature.svg", "Supprimer entité", "clic ou lasso, puis confirmation (clic droit pour valider les clics)"),
    ("hide_layer.svg", "Masquer couche", "clic sur un objet : sa couche est décochée"),
    ("hide_feature.svg", "Masquer entité", "clic sur un objet : il est filtré de sa couche"),
    ("undo.svg", "Annuler", "annule les dernières actions une à une (jusqu'à 50)"),
    ("show_all.svg", "Tout afficher", "recoche les couches et retire les masquages Box_cad"),
    ("hide_all.svg", "Tout éteindre", "décoche toutes les couches vecteur"),
    ("sort_geom.svg", "Trier", "points, lignes puis polygones, par ordre alphabétique"),
    ("to_top.svg", "Ordre des couches", "premier plan, monter, descendre, dernier plan (clic sur un objet)"),
]


DRAW_TOOLS = [
    ("draw_line.svg", "Ligne", "segments enchaînés, longueur et angle tapés"),
    ("draw_polyline.svg", "Polyligne", "une seule entité ; Entrée termine, C ferme"),
    ("draw_rectangle.svg", "Rectangle", "deux coins opposés (largeur, hauteur)"),
    ("draw_rect_rotated.svg", "Rectangle orienté", "premier côté (longueur, angle) puis hauteur"),
    ("draw_square.svg", "Carré", "coin puis côté (longueur, angle)"),
    ("draw_polygon.svg", "Polygone", "un clic par sommet ; Entrée, clic droit ou C ferme"),
    ("draw_circle.svg", "Cercle", "centre puis rayon"),
    ("draw_text.svg", "Texte", "point, angle, puis texte et taille (points, fixe à l'écran)"),
    ("draw_dimension.svg", "Cote", "deux points puis position : ligne de cote fléchée avec la longueur"),
    ("draw_angle.svg", "Angle", "deux lignes (ou Entrée : sommet + 2 points) puis arc : angle en degrés"),
    ("draw_move.svg", "Déplacer / Copier", "objet, point de base, arrivée (copies multiples)"),
    ("draw_rotate.svg", "Rotation", "objet, point de base (Entrée : centre), angle à la souris ou tapé"),
    ("draw_mirror.svg", "Miroir / Échelle", "axe en 2 points ; facteur ou R = référence"),
    ("draw_eraser.svg", "Gomme", "clic ou lasso : efface les formes des couches brouillon (annulable)"),
    ("draw_properties.svg", "Propriétés", "couleur, épaisseur, trait, remplissage ; Entrée : propriétés courantes"),
    ("draw_fill.svg", "Remplissage", "à activer d'un clic : sinon rectangles, carrés et cercles restent vides"),
    ("draw_osnap.svg", "Accrochage objet", "F3 : centre, quadrant, opposé, extrémité, milieu, intersection, perpendiculaire, proche"),
    ("draw_ortho.svg", "Ortho", "F8 : segments horizontaux ou verticaux uniquement"),
]


def _tools_grid(tools):
    """Grille icone + texte : contrairement a un tableau HTML dans un QLabel
    a retour a la ligne, Qt en calcule correctement la hauteur."""
    widget = QWidget()
    grid = QGridLayout(widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(4)
    for row, (icon, name, desc) in enumerate(tools):
        lbl_icon = QLabel()
        lbl_icon.setPixmap(QIcon(os.path.join(PLUGIN_DIR, "icons", icon)).pixmap(20, 20))
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        lbl_text = QLabel(f"<b>{name}</b> : {desc}")
        lbl_text.setWordWrap(True)
        grid.addWidget(lbl_icon, row, 0)
        grid.addWidget(lbl_text, row, 1)
    grid.setColumnStretch(1, 1)
    return widget


def _read_metadata():
    """Lit metadata.txt ligne par ligne (pas de configparser : evite DuplicateOptionError)."""
    vals = {'name': '?', 'version': '?', 'author': '', 'email': '',
            'tracker': '', 'repository': ''}
    meta_path = os.path.join(PLUGIN_DIR, 'metadata.txt')
    if os.path.exists(meta_path):
        with open(meta_path, encoding='utf-8') as f:
            for line in f:
                for k in vals:
                    if line.startswith(k + '='):
                        vals[k] = line.split('=', 1)[1].strip()
                        break
    return vals


def _link(url, text=None):
    return f'<a href="{url}">{text or url.replace("https://", "")}</a>'


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        meta = _read_metadata()
        self.setWindowTitle("À propos de " + meta['name'])
        self.setFixedWidth(540)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        # Logo
        logo_path = os.path.join(PLUGIN_DIR, 'icon.png')
        if os.path.exists(logo_path):
            lbl_logo = QLabel()
            lbl_logo.setPixmap(QPixmap(logo_path).scaledToWidth(
                96, Qt.TransformationMode.SmoothTransformation))
            lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl_logo)

        # Nom + version
        lbl_name = QLabel(meta['name'])
        font_title = QFont()
        font_title.setBold(True)
        font_title.setPointSize(13)
        lbl_name.setFont(font_title)
        lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_name)

        lbl_version = QLabel("Version " + meta['version'])
        lbl_version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_version)

        lbl_desc = QLabel("Nettoyage rapide de l'affichage par clic sur la carte et dessin à la cote, "
                          "inspiré d'AutoCAD. Chaque action est annulable.")
        lbl_desc.setWordWrap(True)
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_desc)

        layout.addWidget(self._separator())

        # Aide-memoire des outils, dans une zone defilante : la fenetre doit tenir
        # sur un petit ecran (720 px de haut)
        help_widget = QWidget()
        help_layout = QVBoxLayout(help_widget)
        help_layout.setContentsMargins(0, 0, 6, 0)
        help_layout.setSpacing(8)
        help_layout.addWidget(_tools_grid(TOOLS))

        lbl_draw = QLabel('<b>Barre Box_cad Dessin</b> — tapez une valeur, Tab : champ suivant, '
                          'Entrée : valider, Échap : annuler. Dessin dans la couche active en édition, '
                          'sinon dans une couche brouillon en mémoire.')
        lbl_draw.setWordWrap(True)
        help_layout.addWidget(lbl_draw)
        help_layout.addWidget(_tools_grid(DRAW_TOOLS))

        lbl_tip = QLabel("Échap annule la sélection en cours de l'outil Supprimer.")
        lbl_tip.setStyleSheet("color:#6b7280")
        lbl_tip.setWordWrap(True)
        help_layout.addWidget(lbl_tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(help_widget)
        layout.addWidget(scroll, 1)

        layout.addWidget(self._separator())

        # Auteur (lien LinkedIn uniquement), licence, depot
        author = _link(LINKEDIN, meta['author'] or "Yoan Laloux")
        infos = [author, _link("https://www.gnu.org/licenses/gpl-3.0.html", "GNU GPL v3")]
        if meta['repository']:
            infos.append(_link(meta['repository']))
        for html in infos:
            lbl = QLabel(html)
            lbl.setOpenExternalLinks(True)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)

        # Boutons Signaler un bug + Fermer
        btn_row = QHBoxLayout()
        if meta['tracker']:
            btn_bug = QPushButton("Signaler un bug")
            btn_bug.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_bug.setStyleSheet(
                "QPushButton{background:#c0392b;color:#fff;border-radius:5px;"
                "font-weight:bold;font-size:11px;padding:5px 10px}"
                "QPushButton:hover{background:#d44534}"
            )
            url_tracker = meta['tracker']
            btn_bug.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url_tracker)))
            btn_row.addWidget(btn_bug)
        btn_row.addStretch()
        btn_close = QPushButton("Fermer")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self.setLayout(layout)

        # Hauteur : tout le contenu si l'ecran le permet, sinon 90 % de l'ecran
        # (la liste des outils defile)
        screen = parent.screen() if parent is not None else None
        avail = screen.availableGeometry().height() if screen is not None else 900
        help_height = help_layout.totalHeightForWidth(480)   # 540 - marges - barre de defilement
        wanted = self.sizeHint().height() - scroll.sizeHint().height() + help_height + 4
        self.resize(self.width(), min(wanted, int(avail * 0.9)))

    @staticmethod
    def _separator():
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        return sep
