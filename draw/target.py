"""Choix de la couche qui recoit les formes dessinees, et couches brouillon."""
from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsField, QgsFillSymbol, QgsGeometry,
    QgsGeometryGeneratorSymbolLayer, QgsLineSymbol, QgsPalLayerSettings, QgsProject,
    QgsNullSymbolRenderer, QgsProperty, QgsSingleSymbolRenderer, QgsSymbolLayer, QgsTextBufferSettings,
    QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QMetaType, QVariant
from qgis.PyQt.QtGui import QColor, QFont

# QGIS 3.30+ a deplace GeometryType dans Qgis.* ; repli sur l'ancien nom
try:
    from qgis.core import Qgis
    LINE = Qgis.GeometryType.Line
    POLYGON = Qgis.GeometryType.Polygon
except AttributeError:
    LINE = QgsWkbTypes.LineGeometry
    POLYGON = QgsWkbTypes.PolygonGeometry

DRAFT_PROPERTY = "box_cad/draft"
STYLE_PROPERTY = "box_cad/style"
STYLE_VERSION = "4"
DRAFT_GROUP = "Box_cad"
DRAFT_NAMES = {
    "line": "Box_cad dessin – lignes",
    "polygon": "Box_cad dessin – polygones",
    "dimension": "Box_cad dessin – cotes",
    "text": "Box_cad dessin – textes",
    "angle": "Box_cad dessin – angles",
}
DRAFT_GEOMETRY = {"line": "LineString", "polygon": "Polygon", "dimension": "MultiLineString",
                  "text": "Point", "angle": "MultiLineString"}

# Proprietes par forme (vides = style par defaut) et valeurs par defaut des expressions
PROPERTY_FIELDS = ("couleur", "epaisseur", "trait", "opacite")
DEFAULT_INK = "#3a3f44"
DEFAULT_FILL = "#ea580c"
STROKE_COLOR = f"coalesce(\"couleur\", '{DEFAULT_INK}')"
DEFAULT_WIDTH = 0.25          # mm, epaisseur des traits par defaut
STROKE_WIDTH = f"coalesce(\"epaisseur\", {DEFAULT_WIDTH})"
STROKE_STYLE = "coalesce(\"trait\", 'solid')"

INK = "58,63,68,255"           # trait des formes
FILL = "234,88,12,90"          # remplissage active (orange translucide)


def _field(name, type_name, length, prec=0):
    """QgsField portable : QMetaType depuis QGIS 3.38 (obligatoire en QGIS 4),
    QVariant avant."""
    try:
        return QgsField(name, getattr(QMetaType.Type, type_name), len=length, prec=prec)
    except TypeError:
        legacy = {"QString": "String", "Double": "Double", "Int": "Int"}[type_name]
        return QgsField(name, getattr(QVariant, legacy), len=length, prec=prec)


def _geometry_type(kind):
    return POLYGON if kind == "polygon" else LINE


def active_target(iface, kind):
    """Couche active si elle est en edition et du bon type, sinon None.
    Cotes, angles et textes vont toujours dans leur brouillon (style fleches / etiquette)."""
    if kind in ("dimension", "text", "angle"):
        return None
    layer = iface.activeLayer()
    if (isinstance(layer, QgsVectorLayer) and layer.isEditable()
            and layer.geometryType() == _geometry_type(kind)):
        return layer
    return None


def draft_layer(kind):
    """Couche memoire brouillon du type demande, creee au premier besoin.
    Retourne (couche, creee?)."""
    project = QgsProject.instance()
    for layer in project.mapLayers().values():
        if layer.customProperty(DRAFT_PROPERTY) == kind:
            _ensure_fields(layer, kind)
            if layer.customProperty(STYLE_PROPERTY) != STYLE_VERSION:
                apply_style(layer, kind)
            return layer, False

    crs = project.crs().authid() or "EPSG:2154"
    layer = QgsVectorLayer(DRAFT_GEOMETRY[kind] + "?crs=" + crs, DRAFT_NAMES[kind], "memory")
    _ensure_fields(layer, kind)
    layer.setCustomProperty(DRAFT_PROPERTY, kind)
    apply_style(layer, kind)

    project.addMapLayer(layer, False)
    root = project.layerTreeRoot()
    group = root.findGroup(DRAFT_GROUP) or root.insertGroup(0, DRAFT_GROUP)
    group.insertLayer(0, layer)
    return layer, True


def _ensure_fields(layer, kind):
    """Champs attendus ; ajoutes (vides) a une couche brouillon d'une version anterieure,
    sans toucher a ses objets."""
    wanted = [_field("outil", "QString", 30)]
    if kind == "text":
        wanted += [_field("texte", "QString", 254), _field("taille", "Double", 6, 1),
                   _field("angle", "Double", 8, 2), _field("couleur", "QString", 9)]
    else:
        wanted += [_field("longueur", "Double", 12, 2), _field("surface", "Double", 14, 2),
                   _field("couleur", "QString", 9), _field("epaisseur", "Double", 5, 2),
                   _field("trait", "QString", 12)]
    if kind == "polygon":
        wanted += [_field("rempli", "Int", 1), _field("opacite", "Int", 3)]
    if kind == "angle":
        wanted += [_field("angle", "Double", 8, 1)]
    missing = [f for f in wanted if layer.fields().indexOf(f.name()) < 0]
    if missing:
        layer.dataProvider().addAttributes(missing)
        layer.updateFields()


# ----------------------------------------------------------------------
# Styles des brouillons
# ----------------------------------------------------------------------

def _prop(name):
    """Propriete de symbole : QgsSymbolLayer.Property.* (3.30+), sinon ancien nom."""
    scoped = getattr(QgsSymbolLayer, "Property", None)
    if scoped is not None:
        return getattr(scoped, name)
    return getattr(QgsSymbolLayer, "Property" + name)


def _line(color=INK, width=str(DEFAULT_WIDTH)):
    return QgsLineSymbol.createSimple({"color": color, "width": width, "capstyle": "round"})


def _stroke_props(symbol_layer, width=True, style=True):
    """Couleur / epaisseur / type de trait pilotes par les champs de la forme."""
    symbol_layer.setDataDefinedProperty(_prop("StrokeColor"), QgsProperty.fromExpression(STROKE_COLOR))
    if width:
        symbol_layer.setDataDefinedProperty(_prop("StrokeWidth"), QgsProperty.fromExpression(STROKE_WIDTH))
    if style:
        symbol_layer.setDataDefinedProperty(_prop("StrokeStyle"), QgsProperty.fromExpression(STROKE_STYLE))


def apply_style(layer, kind):
    if kind == "text":
        layer.setRenderer(QgsNullSymbolRenderer())     # seul le texte (etiquette) est visible
        _text_labels(layer)
        layer.setCustomProperty(STYLE_PROPERTY, STYLE_VERSION)
        layer.triggerRepaint()
        return
    if kind == "polygon":
        symbol = QgsFillSymbol.createSimple({"color": FILL, "outline_color": INK, "outline_width": str(DEFAULT_WIDTH)})
        sl = symbol.symbolLayer(0)
        # vide par defaut : rempli seulement si « rempli » = 1, avec la couleur et l'opacite de la forme
        sl.setDataDefinedProperty(_prop("FillColor"), QgsProperty.fromExpression(
            f"if(\"rempli\" = 1, set_color_part(coalesce(\"couleur\", '{DEFAULT_FILL}'), 'alpha', "
            f"round(coalesce(\"opacite\", 35) * 2.55)), '0,0,0,0')"))
        _stroke_props(sl)
    elif kind == "dimension":
        symbol = _dimension_symbol()
        _dimension_labels(layer)
    elif kind == "angle":
        symbol = _dimension_symbol(curved=True)     # partie 1 = arc (premier, milieu, dernier point)
        _angle_labels(layer)
    else:
        symbol = _line()
        _stroke_props(symbol.symbolLayer(0))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.setCustomProperty(STYLE_PROPERTY, STYLE_VERSION)
    layer.triggerRepaint()


def _dimension_symbol(curved=False):
    """Partie 1 = ligne de cote avec fleches aux deux bouts ; parties 2-3 = lignes de rappel.
    `curved` : fleche en arc de cercle par le premier, le milieu et le dernier point (angles)."""
    from qgis.core import QgsArrowSymbolLayer
    symbol = QgsLineSymbol()
    symbol.deleteSymbolLayer(0)

    rappel = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier": "collect_geometries(geometry_n($geometry, 2), geometry_n($geometry, 3))",
        "SymbolType": "Line"})
    rappel_line = _line("58,63,68,200", "0.25")
    _stroke_props(rappel_line.symbolLayer(0), width=False, style=False)
    rappel.setSubSymbol(rappel_line)
    symbol.appendSymbolLayer(rappel)

    cote = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier": "geometry_n($geometry, 1)", "SymbolType": "Line"})
    arrow_symbol = QgsLineSymbol()
    arrow_symbol.deleteSymbolLayer(0)
    arrow = QgsArrowSymbolLayer.create({
        "arrow_width": "0.25", "arrow_start_width": "0.25", "head_length": "2.2",
        "head_thickness": "0.9", "head_type": "2", "arrow_type": "0",
        "is_curved": "1" if curved else "0", "is_repeated": "0"})
    head = QgsFillSymbol.createSimple({"color": INK, "outline_style": "no"})
    head.symbolLayer(0).setDataDefinedProperty(_prop("FillColor"), QgsProperty.fromExpression(STROKE_COLOR))
    arrow.setSubSymbol(head)
    arrow_symbol.appendSymbolLayer(arrow)
    cote.setSubSymbol(arrow_symbol)
    symbol.appendSymbolLayer(cote)
    return symbol


def _dimension_labels(layer):
    settings = QgsPalLayerSettings()
    settings.fieldName = "format_number(\"longueur\", 2) || ' m'"
    settings.isExpression = True
    settings.geometryGenerator = "geometry_n($geometry, 1)"
    settings.geometryGeneratorEnabled = True
    settings.geometryGeneratorType = LINE
    line_placement = getattr(getattr(Qgis, "LabelPlacement", None), "Line", None)
    settings.placement = line_placement if line_placement is not None else getattr(QgsPalLayerSettings, "Line")
    settings.dist = 0.8
    fmt = QgsTextFormat()        # garder fmt : ne jamais enchainer settings.format().xxx()
    fmt.setFont(QFont("Segoe UI"))
    fmt.setSize(9)
    fmt.setColor(QColor(58, 63, 68))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buffer)
    settings.setFormat(fmt)
    _label_property(settings, "Color", STROKE_COLOR)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def _label_format():
    fmt = QgsTextFormat()        # garder fmt : ne jamais enchainer settings.format().xxx()
    fmt.setFont(QFont("Segoe UI"))
    fmt.setSize(9)
    fmt.setColor(QColor(58, 63, 68))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.8)
    buffer.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buffer)
    return fmt


def _angle_labels(layer):
    """Valeur de l'angle centree sur le milieu de l'arc, a l'horizontale, sur fond blanc."""
    settings = QgsPalLayerSettings()
    settings.fieldName = "format_number(\"angle\", 1) || '°'"
    settings.isExpression = True
    settings.geometryGenerator = ("line_interpolate_point(geometry_n($geometry, 1), "
                                  "length(geometry_n($geometry, 1)) / 2)")
    settings.geometryGeneratorEnabled = True
    point_type = getattr(getattr(Qgis, "GeometryType", None), "Point", None)
    settings.geometryGeneratorType = point_type if point_type is not None else QgsWkbTypes.PointGeometry
    over = getattr(getattr(Qgis, "LabelPlacement", None), "OverPoint", None)
    settings.placement = over if over is not None else getattr(QgsPalLayerSettings, "OverPoint")
    settings.setFormat(_label_format())
    _label_property(settings, "Color", STROKE_COLOR)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def _label_property(settings, name, expression):
    """Propriete d'etiquette pilotee par expression (Property.* depuis 3.30, sinon ancien nom)."""
    scoped = getattr(QgsPalLayerSettings, "Property", None)
    key = getattr(scoped, name) if scoped is not None else getattr(QgsPalLayerSettings, name)
    props = settings.dataDefinedProperties()
    props.setProperty(key, QgsProperty.fromExpression(expression))
    settings.setDataDefinedProperties(props)


def _text_labels(layer):
    """Textes : taille en points (fixe a l'ecran), angle anti-horaire facon AutoCAD,
    point d'insertion en bas a gauche du texte, toujours affiches."""
    settings = QgsPalLayerSettings()
    settings.fieldName = "texte"
    settings.isExpression = False
    over_point = getattr(getattr(Qgis, "LabelPlacement", None), "OverPoint", None)
    settings.placement = over_point if over_point is not None else getattr(QgsPalLayerSettings, "OverPoint")
    quadrant = getattr(getattr(Qgis, "LabelQuadrantPosition", None), "AboveRight", None)
    settings.quadOffset = quadrant if quadrant is not None else getattr(QgsPalLayerSettings, "QuadrantAboveRight")
    fmt = QgsTextFormat()        # garder fmt : ne jamais enchainer settings.format().xxx()
    fmt.setFont(QFont("Segoe UI"))
    fmt.setSize(10)
    fmt.setColor(QColor(58, 63, 68))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(0.7)
    buffer.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buffer)
    settings.setFormat(fmt)
    overlap = getattr(getattr(Qgis, "LabelOverlapHandling", None), "AllowOverlapIfRequired", None)
    if overlap is not None:
        placement = settings.placementSettings()
        placement.setOverlapHandling(overlap)
        settings.setPlacementSettings(placement)
    else:
        settings.displayAll = True
    _label_property(settings, "Size", "coalesce(\"taille\", 10)")
    _label_property(settings, "LabelRotation", "-coalesce(\"angle\", 0)")   # QGIS : sens horaire
    _label_property(settings, "Color", STROKE_COLOR)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


# ----------------------------------------------------------------------
# Ajout des formes
# ----------------------------------------------------------------------

def fit_geometry(geom, layer, source_crs):
    """Adapte une geometrie du canevas a la couche : SCR, multi, Z et M."""
    geom = QgsGeometry(geom)
    if source_crs != layer.crs():
        geom.transform(QgsCoordinateTransform(source_crs, layer.crs(), QgsProject.instance()))
    wkb = layer.wkbType()
    if QgsWkbTypes.isMultiType(wkb):
        geom.convertToMultiType()
    if QgsWkbTypes.hasZ(wkb):
        geom.get().addZValue(0)
    if QgsWkbTypes.hasM(wkb):
        geom.get().addMValue(0)
    return geom


def add_to_draft(layer, geom, tool_name, extra=None):
    """Ajoute directement au fournisseur de la couche brouillon. Retourne le fid ou None.
    `extra` : valeurs d'attributs qui remplacent les mesures par defaut."""
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geom)
    values = {"outil": tool_name, "longueur": round(geom.length(), 2), "surface": round(geom.area(), 2)}
    values.update(extra or {})
    for name, value in values.items():
        if layer.fields().indexOf(name) >= 0:
            feature[name] = value
    ok, added = layer.dataProvider().addFeatures([feature])
    if not ok or not added:
        return None
    layer.updateExtents()
    layer.triggerRepaint()
    return added[0].id()


def add_to_active(iface, layer, geom):
    """Ajoute via les outils de numerisation QGIS : formulaire d'attributs
    (sauf s'il est desactive), commande d'edition annulable par Ctrl+Z."""
    result = iface.vectorLayerTools().addFeature(layer, {}, geom)
    ok = result[0] if isinstance(result, tuple) else bool(result)
    if ok:
        layer.triggerRepaint()
    return ok
