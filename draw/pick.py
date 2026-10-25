"""Recherche des objets sous le curseur ou dans un lasso, partagee par la gomme et les
transformations."""
from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsFeatureRequest, QgsGeometry, QgsPointXY,
    QgsProject, QgsRectangle, QgsVectorLayer,
)

from . import geometry as G
from . import target as T
from .crs import pixel_size

PICK_TOLERANCE = 6      # pixels autour du curseur
TEXT_SEARCH_PX = 600    # un texte peut etre designe loin de son point d'insertion
ANNOTATION_KINDS = ("text", "dimension", "angle")
LABEL_SIZE = 9.0        # points, etiquettes des cotes et des angles (target.py)
LABEL_DIST_MM = 0.8     # ecart etiquette / ligne de cote


def is_text_layer(layer):
    return layer.customProperty(T.DRAFT_PROPERTY) == "text"


def is_annotation_layer(layer):
    """Textes, cotes et angles : dessines par-dessus les formes, prioritaires au clic."""
    return layer.customProperty(T.DRAFT_PROPERTY) in ANNOTATION_KINDS


def _number(value, default):
    try:
        return float(value) if value is not None and str(value) != "NULL" else default
    except (TypeError, ValueError):
        return default


def text_box(canvas, feature, geom=None, angle=None, size=None, crs=None):
    """Cadre approximatif d'un texte (SCR de la couche, `crs`) : depuis le point d'insertion,
    vers la droite et le haut, incline de son angle. Taille en points -> unites du SCR."""
    geom = geom if geom is not None else feature.geometry()
    if geom is None or geom.isNull():
        return QgsGeometry()
    value = feature["texte"]
    text = value if isinstance(value, str) else ""
    lines = text.split("\n") or [""]
    size = size if size is not None else _number(feature["taille"], 10.0)
    angle = angle if angle is not None else _number(feature["angle"], 0.0)
    em = _em(canvas, size, crs)
    width = max(max(len(line) for line in lines), 1) * 0.56 * em
    height = len(lines) * 1.2 * em
    return G.polygon_geometry(G.rect_oriented(geom.asPoint(), angle, width, height))


def hit_geometry(canvas, layer, feature, geom=None, angle=None, size=None):
    """Geometrie qui represente l'objet a l'ecran : cadre pour un texte, sinon la geometrie."""
    if is_text_layer(layer):
        return text_box(canvas, feature, geom, angle, size, layer.crs())
    return geom if geom is not None else feature.geometry()


def _em(canvas, size=LABEL_SIZE, crs=None):
    """Hauteur d'un caractere de `size` points, en unites de `crs` (de la carte par defaut)."""
    return pixel_size(canvas, crs) * canvas.mapSettings().outputDpi() / 72.0 * size


def label_box(canvas, layer, feature, geom=None):
    """Cadre approximatif de l'etiquette d'une cote (le long de la ligne de cote, des deux
    cotes) ou d'un angle (centre sur le milieu de l'arc). Geometrie vide sinon."""
    kind = layer.customProperty(T.DRAFT_PROPERTY)
    geom = geom if geom is not None else feature.geometry()
    if kind not in ("dimension", "angle") or geom is None or geom.isNull():
        return QgsGeometry()
    parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    if not parts or len(parts[0]) < 2:
        return QgsGeometry()
    line = parts[0]
    em = _em(canvas, crs=layer.crs())
    if kind == "dimension":
        a, b = line[0], line[-1]
        chars = len(f"{_number(feature['longueur'], 0):.2f} m")
        width = chars * 0.56 * em
        half = LABEL_DIST_MM * canvas.mapSettings().outputDpi() / 25.4 * pixel_size(canvas, layer.crs()) + 1.2 * em
        ang = G.angle_of(a, b)
        mid = QgsPointXY((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
        corner = G.polar_point(G.polar_point(mid, width / 2, ang + 180), half, ang - 90)
        return G.polygon_geometry(G.rect_oriented(corner, ang, width, 2 * half))
    mid = line[len(line) // 2]                      # angle : etiquette sur le milieu de l'arc
    chars = len(f"{_number(feature['angle'], 0):.1f}°")
    width, height = chars * 0.56 * em, 1.2 * em
    corner = QgsPointXY(mid.x() - width / 2, mid.y() - height / 2)
    return G.polygon_geometry(G.rect_oriented(corner, 0.0, width, height))


def is_hollow(layer, feature):
    """Forme brouillon non remplie : on ne la vise que par son contour ou son centre."""
    if layer.customProperty(T.DRAFT_PROPERTY) != "polygon":
        return False
    idx = layer.fields().indexOf("rempli")      # brouillon d'avant le remplissage : vide
    return idx < 0 or _number(feature.attribute(idx), 0) != 1


def _hollow_distance(geom, pt):
    """Distance au contour ou au centre, la plus petite."""
    boundary = QgsGeometry(geom.constGet().boundary())
    return min(boundary.distance(pt), geom.centroid().distance(pt))


def draft_layers():
    """Couches brouillon de dessin visibles (on n'agit pas sur ce qu'on ne voit pas)."""
    root = QgsProject.instance().layerTreeRoot()
    layers = []
    for layer in QgsProject.instance().mapLayers().values():
        node = root.findLayer(layer.id())
        if layer.customProperty(T.DRAFT_PROPERTY) and node is not None and node.isVisible():
            layers.append(layer)
    return layers


def editable_layers(iface):
    """Brouillons visibles + couche active si elle est vectorielle et en edition."""
    layers = draft_layers()
    active = iface.activeLayer()
    if isinstance(active, QgsVectorLayer) and active.isEditable() and active not in layers:
        layers.append(active)
    return layers


def to_layer(geom, canvas, layer):
    """Geometrie du SCR du canevas vers celui de la couche."""
    canvas_crs = canvas.mapSettings().destinationCrs()
    geom = QgsGeometry(geom)
    if canvas_crs != layer.crs():
        geom.transform(QgsCoordinateTransform(canvas_crs, layer.crs(), QgsProject.instance()))
    return geom


def pick_feature(canvas, layers, map_point):
    """Objet le plus proche du curseur ; si plusieurs polygones le contiennent,
    le plus petit (pour viser une forme dessinee a l'interieur d'une autre).
    Les annotations (texte, cote, angle, etiquette comprise) passent avant les formes
    dans la tolerance : une cote posee dans un cercle reste designable.
    `map_point` est dans le SCR de la carte ; distances comparees en pixels (les
    couches peuvent etre dans des SCR differents).
    Retourne (couche, QgsFeature) ou None."""
    best = None
    for layer in layers:
        upp = pixel_size(canvas, layer.crs())       # un pixel, en unites de la couche
        tol = PICK_TOLERANCE * upp
        pt = to_layer(QgsGeometry.fromPointXY(QgsPointXY(map_point)), canvas, layer)
        p = pt.asPoint()
        if is_text_layer(layer):
            reach = TEXT_SEARCH_PX * upp
        elif is_annotation_layer(layer):
            reach = tol + 3 * _em(canvas, crs=layer.crs())   # etiquette a cote de la ligne de cote / de l'arc
        else:
            reach = tol
        rect = QgsRectangle(p.x() - reach, p.y() - reach, p.x() + reach, p.y() + reach)
        for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
            g = hit_geometry(canvas, layer, f)
            if g.isNull():
                continue
            if g.type() == T.POLYGON and is_hollow(layer, f):
                d = _hollow_distance(g, pt)
            else:
                d = 0.0 if g.contains(pt) else g.distance(pt)
            box = label_box(canvas, layer, f)
            if not box.isNull() and box.contains(pt):
                d = 0.0
            if d > tol:
                continue
            key = (0 if is_annotation_layer(layer) else 1, d / upp, g.area() / (upp * upp))
            if best is None or key < best[0]:
                best = (key, layer, QgsFeature(f))
    return (best[1], best[2]) if best else None


def features_in_lasso(canvas, layers, lasso):
    """Objets touches par le lasso (polygone dans le SCR de la carte) ; un texte compte par
    son cadre. Retourne {layer_id: (couche, [QgsFeature])}."""
    found = {}
    for layer in layers:
        geom = to_layer(lasso, canvas, layer)
        engine = QgsGeometry.createGeometryEngine(geom.constGet())
        engine.prepareGeometry()
        box = geom.boundingBox()
        if is_text_layer(layer):
            box.grow(TEXT_SEARCH_PX * pixel_size(canvas, layer.crs()))
        feats = []
        for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(box)):
            if not f.hasGeometry():
                continue
            hit = hit_geometry(canvas, layer, f)     # variable : la geometrie doit vivre pendant le test
            if engine.intersects(hit.constGet()):
                feats.append(QgsFeature(f))
        if feats:
            found[layer.id()] = (layer, feats)
    return found
