"""ANGLE facon cotation angulaire AutoCAD : deux lignes, ou (Entree) sommet + deux points,
puis position de l'arc. Le cote ou l'on pose l'arc choisit l'angle mesure.

Geometrie enregistree (MultiLineString, brouillon « Box_cad dessin – angles ») :
  partie 1 = arc (fleches aux deux bouts, etiquette = angle en degres au milieu)
  parties suivantes = lignes de rappel, quand l'arc passe au-dela des cotes designes.
"""
from qgis.core import (
    QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsProject, QgsRectangle,
    QgsVectorLayer,
)

from . import geometry as G
from . import target as T
from .draw_tool_base import DrawTool
from .pick import PICK_TOLERANCE

STEP_LINE1, STEP_LINE2, STEP_VERTEX, STEP_P1, STEP_P2, STEP_ARC = range(6)
ARC_STEP_DEG = 3.0          # un sommet tous les 3 degres (minimum 8 segments)
LEN_ANG = [("length", "Longueur", "m"), ("angle", "Angle", "°")]


# ----------------------------------------------------------------------
# Geometrie (fonctions pures, SCR du canevas)
# ----------------------------------------------------------------------

def line_intersection(a1, b1, a2, b2):
    """Intersection des droites (a1, b1) et (a2, b2) ; None si paralleles."""
    d1x, d1y = b1.x() - a1.x(), b1.y() - a1.y()
    d2x, d2y = b2.x() - a2.x(), b2.y() - a2.y()
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-12 * max(1.0, (d1x * d1x + d1y * d1y) * (d2x * d2x + d2y * d2y)):
        return None
    t = ((a2.x() - a1.x()) * d2y - (a2.y() - a1.y()) * d2x) / den
    return QgsPointXY(a1.x() + t * d1x, a1.y() + t * d1y)


def sector(directions, mouse_angle):
    """Secteur (debut, ouverture) delimite par `directions` (degres) qui contient la souris."""
    ds = sorted({round(d % 360.0, 9) for d in directions})
    for i, start in enumerate(ds):
        sweep = (ds[(i + 1) % len(ds)] - start) % 360.0 or 360.0
        if (mouse_angle - start) % 360.0 <= sweep:
            return start, sweep
    return ds[0], 360.0


def _reach(vertex, direction, points):
    """Distance du sommet jusqu'ou la ligne designee va deja dans cette direction."""
    u = G.polar_point(QgsPointXY(0, 0), 1.0, direction)
    proj = [(p.x() - vertex.x()) * u.x() + (p.y() - vertex.y()) * u.y() for p in points]
    return max([0.0] + proj)


def build_angle(vertex, start, sweep, r, reaches):
    """Arc (sens anti-horaire depuis `start`) + rappels la ou l'arc depasse les lignes."""
    n = max(8, int(sweep / ARC_STEP_DEG))
    arc = [G.polar_point(vertex, r, start + sweep * i / n) for i in range(n + 1)]
    parts = [arc]
    for direction, reach in zip((start, start + sweep), reaches):
        if r > reach * 1.0001:
            parts.append([G.polar_point(vertex, reach, direction), G.polar_point(vertex, r, direction)])
    return QgsGeometry.fromMultiPolylineXY(parts)


def angle_geometry(vertex, refs, mouse, radius=None):
    """refs : [(direction, [points de la ligne designee dans cette direction])].
    Retourne (geometrie, angle mesure, rayon) ou None."""
    if G.distance(vertex, mouse) == 0 and not radius:
        return None
    start, sweep = sector([d for d, _ in refs], G.angle_of(vertex, mouse))
    if sweep >= 360.0 or sweep <= 0.0:
        return None
    r = abs(radius) if radius else G.distance(vertex, mouse)
    reaches = []
    for direction in (start, start + sweep):
        points = next((pts for d, pts in refs if abs(((d - direction) + 180.0) % 360.0 - 180.0) < 1e-6), [])
        reaches.append(_reach(vertex, direction, points))
    return build_angle(vertex, start, sweep, r, reaches), sweep, r


def angle_parts(geom):
    """(sommet, debut, ouverture, rayon, [portee des lignes aux deux bouts]) d'un angle
    enregistre, ou None. Sans rappel, la ligne atteignait l'arc : portee = rayon."""
    if geom is None or geom.isNull():
        return None
    parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    if not parts or len(parts[0]) < 3:
        return None
    arc = parts[0]
    a, m, b = arc[0], arc[len(arc) // 2], arc[-1]
    vertex = G.circumcenter(a, m, b)
    if vertex is None:
        return None
    r = G.distance(vertex, a)
    start = G.angle_of(vertex, a)
    sweep = (G.angle_of(vertex, b) - start) % 360.0 or 360.0
    reaches = []
    for end in (a, b):
        rappel = next((p for p in parts[1:] if len(p) == 2 and G.distance(p[1], end) < r * 1e-6), None)
        reaches.append(G.distance(vertex, rappel[0]) if rappel else r)
    return vertex, start, sweep, r, reaches


def moved_angle(geom, dx, dy):
    """Deplacer un angle : le sommet et les cotes restent, l'arc change de rayon."""
    parts = angle_parts(geom)
    if parts is None:
        return None
    vertex, start, sweep, r, reaches = parts
    mid = G.polar_point(vertex, r, start + sweep / 2.0)
    new_r = G.distance(vertex, QgsPointXY(mid.x() + dx, mid.y() + dy))
    if new_r <= 0:
        return None
    return build_angle(vertex, start, sweep, new_r, reaches)


def refs_from_lines(seg1, seg2):
    """Deux lignes : quatre directions depuis l'intersection (angle ou supplementaire)."""
    vertex = line_intersection(*seg1, *seg2)
    if vertex is None:
        return None, []
    refs = []
    for a, b in (seg1, seg2):
        d = G.angle_of(a, b)
        refs += [(d, [a, b]), ((d + 180.0) % 360.0, [a, b])]
    return vertex, refs


def refs_from_points(vertex, p1, p2):
    """Sommet + deux points : l'angle entre les deux cotes ou son complement a 360."""
    return [(G.angle_of(vertex, p1), [p1]), (G.angle_of(vertex, p2), [p2])]


def pick_segment(canvas, map_point):
    """Segment de ligne (ou de contour de polygone) visible le plus proche du curseur,
    dans le SCR du canevas : (a, b) ou None."""
    tol = PICK_TOLERANCE * canvas.mapUnitsPerPixel()
    rect = QgsRectangle(map_point.x() - tol, map_point.y() - tol, map_point.x() + tol, map_point.y() + tol)
    dest = canvas.mapSettings().destinationCrs()
    project = QgsProject.instance()
    best = None
    for layer in canvas.layers():
        if not isinstance(layer, QgsVectorLayer) or layer.geometryType() not in (T.LINE, T.POLYGON):
            continue
        to_layer = QgsCoordinateTransform(dest, layer.crs(), project)
        back = QgsCoordinateTransform(layer.crs(), dest, project)
        request = QgsFeatureRequest().setFilterRect(to_layer.transformBoundingBox(rect)).setNoAttributes().setLimit(200)
        for feature in layer.getFeatures(request):
            geom = QgsGeometry(feature.geometry())
            if geom.isNull():
                continue
            if layer.crs() != dest:
                geom.transform(back)
            sqr, _, after, _ = geom.closestSegmentWithContext(map_point)
            if after <= 0 or sqr < 0 or sqr > tol * tol:
                continue
            if best is None or sqr < best[0]:
                a, b = geom.vertexAt(after - 1), geom.vertexAt(after)
                best = (sqr, (QgsPointXY(a.x(), a.y()), QgsPointXY(b.x(), b.y())))
    return best[1] if best else None


# ----------------------------------------------------------------------
# Outil
# ----------------------------------------------------------------------

class AngleTool(DrawTool):
    TOOL_NAME = "angle"

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.rb_extra = None
        self.segments = []
        self.vertex = None
        self.refs = []
        self.hover_segment = None
        self.preview = None

    def deactivate(self):
        if self.rb_extra is not None:
            self.canvas().scene().removeItem(self.rb_extra)
            self.rb_extra = None
        super().deactivate()

    def start(self):
        if self.rb_extra is None:
            self.rb_extra = self._rubber(T.LINE)
        self.points = []
        self.segments = []
        self.vertex = None
        self.refs = []
        self.preview = None
        self.step = STEP_LINE1
        self.configure("ANGLE · cliquez une première ligne (Entrée : sommet et deux points) :")

    def wants_osnap(self):
        return self.step not in (STEP_LINE1, STEP_LINE2)

    def compute(self, mouse):
        self.hover_segment = None
        self.preview = None
        if self.step in (STEP_LINE1, STEP_LINE2):
            self.hover_segment = pick_segment(self.canvas(), mouse)
            return mouse, None, None, {}, ""
        if self.step == STEP_VERTEX:
            return mouse, None, None, {}, ""
        if self.step in (STEP_P1, STEP_P2):
            pt, ang, dist = self.constrain_from(self.vertex, mouse)
            preview = [self.vertex, pt] if self.step == STEP_P1 else [self.points[1], self.vertex, pt]
            return pt, preview, "line", {"length": dist, "angle": ang}, ""
        result = angle_geometry(self.vertex, self.refs, mouse, self.dyn.locked("radius"))
        if result is None:
            return mouse, None, None, {}, ""
        self.preview, sweep, r = result
        return mouse, None, None, {"radius": r}, f"Angle {sweep:.1f}°".replace(".", ",")

    def draw_extra(self):
        if self.rb_extra is None:
            return
        self.rb_extra.reset(T.LINE)
        parts = [list(s) for s in self.segments]
        if self.hover_segment is not None:
            parts.append(list(self.hover_segment))
        if parts:
            self.rb_extra.setToGeometry(QgsGeometry.fromMultiPolylineXY(parts), None)
        if self.preview is not None:
            self.rb_extra.addGeometry(self.preview, None)

    def on_click(self, pt):
        if self.step in (STEP_LINE1, STEP_LINE2):
            seg = pick_segment(self.canvas(), pt)
            if seg is None:
                return                               # rien sous le curseur : on attend une ligne
            self.segments.append(seg)
            if self.step == STEP_LINE1:
                self.step = STEP_LINE2
                self.configure("Seconde ligne :")
                return
            vertex, refs = refs_from_lines(*self.segments)
            if vertex is None:
                self.plugin.warn("Ces deux lignes sont parallèles : pas d'angle à mesurer.")
                self.segments.pop()
                return
            self.vertex, self.refs = vertex, refs
            self._ask_arc()
        elif self.step == STEP_VERTEX:
            self.vertex = QgsPointXY(pt)
            self.points = [self.vertex]
            self.step = STEP_P1
            self.configure("Premier côté :", LEN_ANG)
        elif self.step == STEP_P1:
            if G.distance(self.vertex, pt) == 0:
                return
            self.points.append(QgsPointXY(pt))
            self.step = STEP_P2
            self.configure("Second côté :", LEN_ANG)
        elif self.step == STEP_P2:
            if G.distance(self.vertex, pt) == 0:
                return
            self.points.append(QgsPointXY(pt))
            self.refs = refs_from_points(self.vertex, self.points[1], self.points[2])
            self._ask_arc()
        else:
            self._commit_angle(self.mouse)

    def _ask_arc(self):
        self.step = STEP_ARC
        self.points = [self.vertex]
        self.configure("Position de l'arc (le côté choisit l'angle) :", [("radius", "Rayon", "m")])

    def on_finish(self, cancel=False):
        if not cancel and self.step == STEP_LINE1:
            self.step = STEP_VERTEX                  # Entree : mode sommet + deux points
            self.configure("ANGLE · sommet :")
            return
        if not cancel and self.step == STEP_ARC and self.mouse is not None:
            self._commit_angle(self.mouse)
            return
        self.start()

    def undo_point(self):
        self.start()

    def _commit_angle(self, mouse):
        result = angle_geometry(self.vertex, self.refs, mouse, self.dyn.locked("radius"))
        if result is None:
            return
        geom, sweep, r = result
        self.commit(geom, "angle", {"angle": round(sweep, 1), "longueur": round(r, 2), "surface": 0})
        self.start()
