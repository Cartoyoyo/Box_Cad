"""COTE OBJET : un clic sur une forme dessinee (ou un objet de la couche active en edition),
puis la position de la cote. La cote porte la LONGUEUR TOTALE de l'objet (polyligne : somme de
ses segments ; polygone, cercle : perimetre), et non la distance entre ses deux extremites.

- polyligne (une seule partie, au moins 3 sommets) : la ligne de cote SUIT la polyligne, decalee de
  la distance choisie a la souris (ou tapee), avec une pointe a chaque bout et deux lignes de rappel ;
- segment seul, forme fermee, objet en plusieurs parties : cote droite entre le premier et le dernier
  sommet (pour une forme fermee : le premier et le plus eloigne).

La cote est associee a l'objet : elle le suit quand on le deplace, le tourne, le copie ou le met
a l'echelle (cotes associatives de Box_cad)."""
from qgis.core import QgsPointXY
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtGui import QColor

from . import geometry as G
from . import target as T
from .dimension import (
    STEP_OFFSET, DimensionTool, _signed, clean_path, dimension_geometry, path_dimension_geometry, path_offset_at,
)
from .pick import editable_layers, hit_geometry, is_annotation_layer, pick_feature

STEP_PICK = 3
HOVER = QColor(37, 99, 235)


def object_ends(geom):
    """(point 1, point 2, longueur totale, chemin) d'un objet, SCR de travail. `chemin` est la
    polyligne a suivre (liste de points), ou None pour une cote droite. None si l'objet n'est
    ni une ligne ni un polygone, ou s'il est degenere."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    kind = geom.type()
    path = None
    if kind == T.LINE:
        parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
        parts = [p for p in parts if len(p) >= 2]
        if not parts:
            return None
        vertices = [QgsPointXY(v) for p in parts for v in p]
        p1, p2 = QgsPointXY(parts[0][0]), QgsPointXY(parts[-1][-1])
        if len(parts) == 1:
            clean = clean_path(parts[0])
            if len(clean) >= 3 and G.distance(clean[0], clean[-1]) > 0:
                path = clean
                p1, p2 = clean[0], clean[-1]
    elif kind == T.POLYGON:
        rings = geom.asMultiPolygon() if geom.isMultipart() else [geom.asPolygon()]
        if not rings or not rings[0] or len(rings[0][0]) < 3:
            return None
        vertices = [QgsPointXY(v) for v in rings[0][0]]
        p1, p2 = vertices[0], vertices[0]
    else:
        return None
    if G.distance(p1, p2) == 0:          # forme fermee : on cote vers le sommet le plus eloigne
        p2 = max(vertices, key=lambda v: G.distance(p1, v))
    if G.distance(p1, p2) == 0:
        return None
    return p1, p2, geom.length(), path


class ObjectDimensionTool(DimensionTool):
    NAME = "Cote objet"
    TOOL_NAME = "cote"
    GUIDE_STEPS = ("Objet", "Position")
    GUIDE_HINTS = ("Cliquez une forme dessinée, ou un objet de la couche active en édition : "
                   "la cote portera sa <b>longueur totale</b>.",
                   "Placez la ligne de cote à la souris (elle suit l'objet) et cliquez, ou tapez le "
                   "<b>décalage</b> ; <b>Entrée</b> : cote posée sur l'objet.")
    GUIDE_KEYS = "U : choisir un autre objet · F8 : ortho · F3 : accrochage objet · Échap : recommencer"

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.hover = None        # (couche, QgsFeature) sous le curseur
        self.total = None        # longueur totale de l'objet designe
        self.path = None         # polyligne suivie par la cote, ou None (cote droite)
        self.rb_hover = None

    def activate(self):
        super().activate()
        self.rb_hover = QgsRubberBand(self.canvas(), T.LINE)
        self.rb_hover.setStrokeColor(HOVER)
        self.rb_hover.setWidth(4)

    def deactivate(self):
        if self.rb_hover is not None:
            self.canvas().scene().removeItem(self.rb_hover)
            self.rb_hover = None
        super().deactivate()

    def wants_osnap(self):
        return self.step != STEP_PICK          # pas d'accrochage pour designer l'objet

    def start(self):
        self.points = []
        self.hover = None
        self.total = None
        self.path = None
        self.step = STEP_PICK
        self.configure("COTE OBJET · cliquez l'objet :")

    def _candidates(self):
        return [layer for layer in editable_layers(self.plugin.iface) if not is_annotation_layer(layer)]

    def _offset(self, mouse):
        """Decalage de la ligne de cote : distance signee a l'objet (tapee si verrouillee)."""
        if self.path is not None:
            return _signed(self.dyn.locked("offset"), path_offset_at(self.path, mouse))
        p1, p2 = self.points
        return _signed(self.dyn.locked("offset"), G.signed_offset(p1, G.angle_of(p1, p2), mouse))

    def compute(self, mouse):
        if self.step == STEP_PICK:
            self.hover = pick_feature(self.canvas(), self._candidates(), self.frame.to_map(mouse))
            return mouse, None, None, {}, ""
        info = f"Cote objet {self.total:.2f} m".replace(".", ",")
        offset = self._offset(mouse)
        if self.path is None:
            _, d1, d2 = dimension_geometry(self.points[0], self.points[1], offset)
            return d2, [self.points[0], d1, d2, self.points[1]], "line", {"offset": abs(offset)}, info
        parts = path_dimension_geometry(self.path, offset).asMultiPolyline()
        line = [QgsPointXY(v) for v in parts[0]]
        preview = [self.path[0]] + line + [self.path[-1]] if len(parts) >= 3 else line
        return line[-1], preview, "line", {"offset": abs(offset)}, info

    def draw_extra(self):
        if self.rb_hover is None:
            return
        self.rb_hover.reset(T.LINE)
        if self.step == STEP_PICK and self.hover is not None:
            layer, feature = self.hover
            geom = hit_geometry(self.canvas(), layer, feature)
            if not geom.isNull():
                self.rb_hover.setToGeometry(geom, layer)

    def on_click(self, pt):
        if self.step != STEP_PICK:
            super().on_click(pt)
            return
        if self.hover is None:
            self.plugin.warn("Cote objet : cliquez une forme dessinée, "
                             "ou un objet de la couche active en édition.")
            return
        layer, feature = self.hover
        ends = object_ends(self.frame.from_layer(feature.geometry(), layer))
        if ends is None:
            self.plugin.warn("Cote objet : cet objet n'est ni une ligne ni un polygone.")
            return
        p1, p2, self.total, self.path = ends
        self.points = [p1, p2]
        self.hover = None
        self.step = STEP_OFFSET
        self.configure("Position de la cote (Entrée : sans décalage) :", [("offset", "Décalage", "m")])

    def undo_point(self):
        self.start()

    def _commit_dimension(self, mouse):
        p1, p2 = self.points
        offset = 0.0 if mouse is None else self._offset(mouse)
        if self.path is not None:
            geom = path_dimension_geometry(self.path, offset)
        else:
            geom, _, _ = dimension_geometry(p1, p2, offset)
        self.commit(geom, "dimension", {"longueur": round(self.total, 2), "surface": 0})
        self.start()
