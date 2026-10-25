"""Accrochage objet facon AutoCAD (OSNAP), independant de l'accrochage QGIS.

Points proposes (si le mode est coche) : extremite, milieu, centre (cercle exact ou
centre geometrique d'un polygone), quadrant, proche, oppose (diametralement oppose
au point precedent), intersection, perpendiculaire.

Les cercles sont reconnus a la forme : anneau d'au moins 24 sommets equidistants
du barycentre. Centre et rayon sont alors exacts (cercles Box_cad, tampons...).
"""
from qgis.core import (
    QgsCoordinateTransform, QgsCsException, QgsFeatureRequest, QgsGeometry,
    QgsPointLocator, QgsPointXY, QgsProject, QgsRectangle, QgsVectorLayer, QgsWkbTypes,
)

from . import geometry as G
from .crs import pixel_size

MODES = [
    ("endpoint", "Extrémité"), ("midpoint", "Milieu"), ("center", "Centre"),
    ("quadrant", "Quadrant"), ("nearest", "Proche"), ("opposite", "Opposé"),
    ("intersection", "Intersection"), ("perpendicular", "Perpendiculaire"),
]
LABELS = dict(MODES)
TOLERANCE_PX = 12
MAX_FEATURES_PER_LAYER = 40
MAX_VERTICES = 600          # au-dela : geometrie decoupee a la zone du curseur
CENTER_RADIUS_RATIO = 0.25  # centre d'un cercle propose a moins d'1/4 du rayon

# classes de priorite : 0 = point precis dans la tolerance, 1 = objet survole
# (oppose, perpendiculaire), 2 = proche
POINT_CLASS, OBJECT_CLASS, NEAREST_CLASS = 0, 1, 2
# a distance egale (meme point) : etiquette la plus parlante
PRIORITY = ["intersection", "endpoint", "center", "quadrant", "opposite", "midpoint",
            "perpendicular", "nearest"]


class Shapes:
    """Elements geometriques utiles a l'accrochage, dans le SCR de travail des outils."""

    def __init__(self):
        self.vertices = []      # extremites
        self.segments = []      # (a, b) hors cercles
        self.circles = []       # (centre, rayon)
        self.centroids = []     # centres geometriques des polygones (hors cercles)

    def add_geometry(self, geom, clip_rect=None):
        if geom is None or geom.isNull():
            return
        if QgsWkbTypes.isCurvedType(geom.wkbType()):
            geom = QgsGeometry(geom.constGet().segmentize())
        big = clip_rect is not None and _vertex_count(geom) > MAX_VERTICES
        if big:
            geom = geom.clipped(clip_rect)      # gros polygone (commune...) : zone utile seule
            if geom.isNull():
                return
        gtype = geom.type()
        multi = QgsWkbTypes.isMultiType(geom.wkbType())
        if gtype == G_POINT:
            pts = geom.asMultiPoint() if multi else [geom.asPoint()]
            self.vertices.extend(QgsPointXY(p) for p in pts)
        elif gtype == G_LINE:
            for line in (geom.asMultiPolyline() if multi else [geom.asPolyline()]):
                self._add_path(line)
        elif gtype == G_POLYGON:
            for poly in (geom.asMultiPolygon() if multi else [geom.asPolygon()]):
                for ring in poly:
                    circle = None if big else G.circle_of_ring(ring)
                    if circle:
                        self.circles.append(circle)
                    else:
                        self._add_path(ring)
                if poly and not big and not G.circle_of_ring(poly[0]):
                    c = QgsGeometry.fromPolygonXY(poly).centroid()
                    if not c.isNull():
                        self.centroids.append(c.asPoint())

    def _add_path(self, pts):
        pts = [QgsPointXY(p) for p in pts]
        closed = len(pts) > 2 and pts[0] == pts[-1]
        self.vertices.extend(pts[:-1] if closed else pts)
        self.segments.extend(zip(pts[:-1], pts[1:]))


def _vertex_count(geom):
    return geom.constGet().nCoordinates()


try:
    from qgis.core import Qgis
    G_POINT, G_LINE, G_POLYGON = Qgis.GeometryType.Point, Qgis.GeometryType.Line, Qgis.GeometryType.Polygon
except AttributeError:
    G_POINT, G_LINE, G_POLYGON = QgsWkbTypes.PointGeometry, QgsWkbTypes.LineGeometry, QgsWkbTypes.PolygonGeometry


class SnapCache:
    """Sources d'objets pour l'accrochage.

    - Couches memoire (brouillons Box_cad) : lues directement (rapides) ; geometrie
      complete, donc cercles exacts (centre, quadrants, oppose) et centres de polygones.
    - Autres couches : index QgsPointLocator APPARTENANT A BOX_CAD (un par couche),
      construits en arriere-plan puis interroges en ~1 ms (sommets et segments).

    Regles de surete (plantages 0xc0000374 du 16/10/2026 en lisant les index geres par
    QGIS, que QGIS peut detruire ou reconstruire a tout moment) :
      * jamais d'index de canvas.snappingUtils() : uniquement les notres ;
      * interrogation seulement si l'index est pret (hasIndex et pas isIndexing) ;
      * avant de detruire un index : attendre la fin de son indexation ;
      * index detruit AVANT la couche (layersWillBeRemoved), a la fermeture du projet
        et au dechargement du plugin.
    """

    def __init__(self, canvas):
        self.canvas = canvas
        self.locators = {}          # layer_id -> (QgsPointLocator, authid du SCR)
        project = QgsProject.instance()
        project.layersWillBeRemoved.connect(self._drop_layers)
        project.cleared.connect(self.clear)

    def shutdown(self):
        """Dechargement du plugin : deconnexion et liberation de tous les index."""
        project = QgsProject.instance()
        for signal, slot in ((project.layersWillBeRemoved, self._drop_layers), (project.cleared, self.clear)):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass
        self.clear()

    def clear(self, *args):
        for layer_id in list(self.locators):
            self._delete(layer_id)

    def _drop_layers(self, layer_ids):
        for layer_id in layer_ids:
            self._delete(layer_id)

    def _delete(self, layer_id):
        entry = self.locators.pop(layer_id, None)
        if entry is None:
            return
        locator = entry[0]
        if locator.isIndexing():
            locator.waitForIndexingFinished()   # ne jamais detruire pendant l'indexation
        del locator

    def add_to(self, shapes, layer, rect, crs):
        """Objets de `layer` dans `rect`, exprimes dans `crs` (SCR de travail)."""
        if layer.providerType() == "memory":
            for geom in _read(layer, rect, crs, MAX_FEATURES_PER_LAYER):
                shapes.add_geometry(geom, rect)
            return
        entry = self.locators.get(layer.id())
        if entry is not None and entry[1] != crs.authid():
            self._delete(layer.id())            # SCR de travail change : nouvel index
            entry = None
        if entry is None:
            locator = QgsPointLocator(layer, crs, QgsProject.instance().transformContext())
            self.locators[layer.id()] = (locator, crs.authid())
            locator.init(-1, True)              # asynchrone : rend la main tout de suite
            return
        locator = entry[0]
        if locator.isIndexing():
            return                              # index en construction : pas encore accrochable
        if not locator.hasIndex():
            locator.init(-1, True)              # index invalide par une modification de la couche
            return
        for match in locator.verticesInRect(rect, None, True):
            shapes.vertices.append(QgsPointXY(match.point()))
        for match in locator.edgesInRect(rect, None, True):
            a, b = match.edgePoints()
            shapes.segments.append((QgsPointXY(a), QgsPointXY(b)))


def _read(layer, rect, crs, limit):
    """Lecture directe des geometries de `layer` dans `rect`, rendues dans `crs`."""
    same = crs == layer.crs()
    project = QgsProject.instance()
    if same:
        layer_rect = rect
    else:
        try:
            layer_rect = QgsCoordinateTransform(crs, layer.crs(), project).transformBoundingBox(rect)
        except QgsCsException:          # emprise hors du domaine de validite du SCR
            return []
        to_canvas = QgsCoordinateTransform(layer.crs(), crs, project)
    request = QgsFeatureRequest().setFilterRect(layer_rect).setNoAttributes().setLimit(limit)
    geoms = []
    for feature in layer.getFeatures(request):
        geom = QgsGeometry(feature.geometry())
        if geom.isNull():
            continue
        if not same:
            geom.transform(to_canvas)
        geoms.append(geom)
    return geoms


class ObjectSnapper:
    def __init__(self, canvas, plugin):
        self.canvas = canvas
        self.plugin = plugin
        self.cache = getattr(plugin, "osnap_cache", None) or SnapCache(canvas)

    # ------------------------------------------------------------------
    # Collecte des objets proches du curseur
    # ------------------------------------------------------------------

    def collect(self, point, tol, crs):
        rect = QgsRectangle(point.x() - tol, point.y() - tol, point.x() + tol, point.y() + tol)
        scale = self.canvas.scale()
        shapes = Shapes()
        for layer in self.canvas.layers():
            if not isinstance(layer, QgsVectorLayer) or not layer.isSpatial():
                continue
            if layer.hasScaleBasedVisibility() and not layer.isInScaleRange(scale):
                continue
            self.cache.add_to(shapes, layer, rect, crs)
        return shapes

    # ------------------------------------------------------------------
    # Recherche du point d'accrochage
    # ------------------------------------------------------------------

    def find(self, point, last_point=None, modes=None, crs=None):
        """`point` et `last_point` dans `crs` (SCR de travail ; celui de la carte par
        defaut). Retourne (point, mode) ou None, dans ce meme SCR."""
        modes = set(modes if modes is not None else self.plugin.osnap_modes)
        if not modes:
            return None
        crs = crs if crs is not None else self.canvas.mapSettings().destinationCrs()
        tol = TOLERANCE_PX * pixel_size(self.canvas, crs)
        shapes = self.collect(point, tol, crs)
        return pick(shapes, point, last_point, modes, tol)


def pick(shapes, point, last_point, modes, tol):
    """Choix du meilleur point d'accrochage (fonction pure, testable)."""
    cands = []      # (classe, distance, point, mode)

    def add(cls, p, mode):
        if mode in modes:
            cands.append((cls, G.distance(point, p), QgsPointXY(p), mode))

    near_segments = [s for s in shapes.segments
                     if G.distance(point, G.project_on_segment(point, *s)[0]) <= tol]
    on_circles = [c for c in shapes.circles if abs(G.distance(point, c[0]) - c[1]) <= tol]

    for v in shapes.vertices:
        if G.distance(point, v) <= tol:
            add(POINT_CLASS, v, "endpoint")
    for a, b in near_segments:
        mid = QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
        if G.distance(point, mid) <= tol:
            add(POINT_CLASS, mid, "midpoint")
    for c in shapes.centroids:
        if G.distance(point, c) <= tol:
            add(POINT_CLASS, c, "center")
    for center, r in shapes.circles:
        if G.distance(point, center) <= max(tol, CENTER_RADIUS_RATIO * r):
            add(POINT_CLASS, center, "center")
        for ang in (0, 90, 180, 270):
            q = G.polar_point(center, r, ang)
            if G.distance(point, q) <= tol:
                add(POINT_CLASS, q, "quadrant")

    # intersections (segments et cercles sous le curseur)
    if "intersection" in modes:
        for p in _intersections(near_segments, on_circles):
            if G.distance(point, p) <= tol:
                add(POINT_CLASS, p, "intersection")

    if last_point is not None:
        for center, r in shapes.circles:
            # oppose : le point precedent est sur ce cercle
            if r > 0 and abs(G.distance(last_point, center) - r) <= 1e-6 * max(r, 1.0):
                opp = QgsPointXY(2 * center.x() - last_point.x(), 2 * center.y() - last_point.y())
                if G.distance(point, opp) <= tol:
                    add(POINT_CLASS, opp, "opposite")
                elif (center, r) in on_circles:
                    add(OBJECT_CLASS, opp, "opposite")
        # perpendiculaire depuis le point precedent
        for a, b in near_segments:
            foot, t = G.foot_on_line(last_point, a, b)
            if foot is not None and 0.0 <= t <= 1.0 and G.distance(foot, last_point) > 0:
                add(OBJECT_CLASS, foot, "perpendicular")
        for center, r in on_circles:
            d = G.distance(last_point, center)
            if d > 0:
                add(OBJECT_CLASS, G.polar_point(center, r, G.angle_of(center, last_point)), "perpendicular")

    # proche : sur un segment ou sur la circonference
    for a, b in near_segments:
        add(NEAREST_CLASS, G.project_on_segment(point, a, b)[0], "nearest")
    for center, r in on_circles:
        if G.distance(point, center) > 0:
            add(NEAREST_CLASS, G.polar_point(center, r, G.angle_of(center, point)), "nearest")

    if not cands:
        return None
    best = min(cands, key=lambda c: (c[0], round(c[1], 9), PRIORITY.index(c[3])))
    return best[2], best[3]


def _intersections(segments, circles):
    points = []
    for i, (a, b) in enumerate(segments):
        for c, d in segments[i + 1:]:
            if a in (c, d) or b in (c, d):     # segments consecutifs : c'est un sommet
                continue
            p = G.segment_intersection(a, b, c, d)
            if p is not None:
                points.append(p)
        for center, r in circles:
            points.extend(G.segment_circle_intersections(a, b, center, r))
    for i, (c1, r1) in enumerate(circles):
        for c2, r2 in circles[i + 1:]:
            points.extend(G.circle_circle_intersections(c1, r1, c2, r2))
    return points
