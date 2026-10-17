"""COTE facon cotation alignee AutoCAD : point 1 -> point 2 -> position de la cote.

Geometrie enregistree (MultiLineString, brouillon « Box_cad dessin – cotes ») :
  partie 1 = ligne de cote (fleches aux deux bouts, etiquette = longueur mesuree)
  parties 2 et 3 = lignes de rappel (absentes si la cote est posee sans decalage).
"""
from qgis.core import QgsGeometry, QgsPointXY

from . import geometry as G
from .draw_tool_base import DrawTool

STEP_P1, STEP_P2, STEP_OFFSET = 0, 1, 2


def _signed(locked, measured):
    if locked is None:
        return measured
    return abs(locked) if measured >= 0 else -abs(locked)


def dimension_geometry(p1, p2, offset):
    """Ligne de cote parallele a p1-p2, decalee de `offset` (positif = a gauche)."""
    ang = G.angle_of(p1, p2)
    d1 = G.polar_point(p1, offset, ang + 90.0)
    d2 = G.polar_point(p2, offset, ang + 90.0)
    parts = [[QgsPointXY(d1), QgsPointXY(d2)]]
    if abs(offset) > 0:
        parts += [[QgsPointXY(p1), QgsPointXY(d1)], [QgsPointXY(p2), QgsPointXY(d2)]]
    return QgsGeometry.fromMultiPolylineXY(parts), d1, d2


def dimension_parts(geom):
    """(point 1, point 2, decalage signe) d'une cote enregistree, ou None."""
    if geom is None or geom.isNull():
        return None
    parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    if not parts or len(parts[0]) < 2:
        return None
    d1, d2 = parts[0][0], parts[0][-1]
    p1, p2 = (parts[1][0], parts[2][0]) if len(parts) >= 3 else (d1, d2)
    if G.distance(p1, p2) == 0:
        return None
    return QgsPointXY(p1), QgsPointXY(p2), G.signed_offset(p1, G.angle_of(p1, p2), d1)


def moved_dimension(geom, dx, dy):
    """Deplacer une cote : les points mesures restent, la ligne de cote suit le decalage."""
    parts = dimension_parts(geom)
    if parts is None:
        return None
    p1, p2, offset = parts
    ang = G.angle_of(p1, p2)
    d1 = G.polar_point(p1, offset, ang + 90.0)
    offset = G.signed_offset(p1, ang, QgsPointXY(d1.x() + dx, d1.y() + dy))
    return dimension_geometry(p1, p2, offset)[0]


class DimensionTool(DrawTool):
    TOOL_NAME = "cote"

    def start(self):
        self.points = []
        self.step = STEP_P1
        self.configure("COTE · premier point :")

    def compute(self, mouse):
        if self.step == STEP_P1:
            return mouse, None, None, {}, ""
        p1 = self.points[0]
        if self.step == STEP_P2:
            pt, ang, dist = self.constrain_from(p1, mouse)
            return pt, [p1, pt], "line", {"length": dist, "angle": ang}, ""
        p2 = self.points[1]
        ang = G.angle_of(p1, p2)
        offset = _signed(self.dyn.locked("offset"), G.signed_offset(p1, ang, mouse))
        _, d1, d2 = dimension_geometry(p1, p2, offset)
        length = G.distance(p1, p2)
        info = f"Cote {length:.2f} m".replace(".", ",")
        # apercu en U : rappel 1, ligne de cote, rappel 2
        return d2, [p1, d1, d2, p2], "line", {"offset": abs(offset)}, info

    def on_click(self, pt):
        if self.step == STEP_P1:
            self.points = [QgsPointXY(pt)]
            self.step = STEP_P2
            self.configure("Second point (longueur, angle) :",
                           [("length", "Longueur", "m"), ("angle", "Angle", "°")])
        elif self.step == STEP_P2:
            if G.distance(self.points[0], pt) == 0:
                return
            self.points.append(QgsPointXY(pt))
            self.step = STEP_OFFSET
            self.configure("Position de la cote (Entrée : sans décalage) :", [("offset", "Décalage", "m")])
        else:
            self._commit_dimension(self.mouse)

    def on_finish(self, cancel=False):
        if not cancel and self.step == STEP_OFFSET:
            self._commit_dimension(None)       # Entree sans saisie : cote posee sur les points
            return
        self.start()

    def undo_point(self):
        if self.step == STEP_OFFSET:
            self.points.pop()
            self.step = STEP_P2
            self.configure("Second point (longueur, angle) :",
                           [("length", "Longueur", "m"), ("angle", "Angle", "°")])
        else:
            self.start()

    def _commit_dimension(self, mouse):
        p1, p2 = self.points
        offset = 0.0
        if mouse is not None:
            ang = G.angle_of(p1, p2)
            offset = _signed(self.dyn.locked("offset"), G.signed_offset(p1, ang, mouse))
        geom, _, _ = dimension_geometry(p1, p2, offset)
        self.commit(geom, "dimension", {"longueur": round(G.distance(p1, p2), 2), "surface": 0})
        self.start()
