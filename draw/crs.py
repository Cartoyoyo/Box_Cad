"""SCR de travail des outils de dessin : des metres vrais quel que soit le SCR du projet.

La carte peut etre en degres (WGS 84) ou dans une projection qui deforme les longueurs
(Web Mercator : x1,44 a la latitude de Vichy). Les outils calculent alors dans un SCR
metrique choisi tout seul : Lambert 93 en France metropolitaine, sinon la zone UTM du
centre de la carte. Si le SCR de la carte donne deja des metres vrais, c'est lui, et
toutes les conversions ci-dessous rendent leur entree inchangee.
"""
import math

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCsException, QgsDistanceArea,
    QgsGeometry, QgsPointXY, QgsProject, QgsUnitTypes,
)

from . import geometry as G

MAX_SCALE_ERROR = 0.01      # 1 % : au-dela, une unite de la carte n'est plus un metre vrai
LAMBERT93 = "EPSG:2154"
WGS84 = "EPSG:4326"
DEFAULT_ELLIPSOID = "EPSG:7030"     # WGS 84

try:
    from qgis.core import Qgis
    METERS = Qgis.DistanceUnit.Meters               # QGIS 3.30+
except AttributeError:
    METERS = QgsUnitTypes.DistanceMeters


def scale_error(crs, point, step=100.0):
    """Ecart relatif entre une longueur mesuree sur la carte et sa longueur vraie
    (ellipsoide), autour de `point`, dans les deux directions."""
    da = QgsDistanceArea()
    da.setSourceCrs(crs, QgsProject.instance().transformContext())
    if not da.setEllipsoid(crs.ellipsoidAcronym() or DEFAULT_ELLIPSOID):
        da.setEllipsoid(DEFAULT_ELLIPSOID)
    worst = 0.0
    for dx, dy in ((step, 0.0), (0.0, step)):
        true = da.measureLine(QgsPointXY(point), QgsPointXY(point.x() + dx, point.y() + dy))
        if not true or true <= 0:
            return math.inf
        worst = max(worst, abs(step / true - 1.0))
    return worst


def is_metric(crs, point):
    """Projection en metres qui ne deforme pas les longueurs (a 1 % pres) autour de `point`."""
    return (crs.isValid() and not crs.isGeographic() and crs.mapUnits() == METERS
            and scale_error(crs, point) <= MAX_SCALE_ERROR)


def metric_crs_at(lon, lat):
    """Lambert 93 si le point est dans son domaine (France metropolitaine), sinon sa zone UTM."""
    l93 = QgsCoordinateReferenceSystem(LAMBERT93)
    if l93.bounds().contains(QgsPointXY(lon, lat)):
        return l93
    zone = min(60, max(1, int((lon + 180.0) // 6.0) + 1))
    return QgsCoordinateReferenceSystem(f"EPSG:{(32600 if lat >= 0 else 32700) + zone}")


def work_crs(canvas):
    """SCR de calcul : celui de la carte s'il donne des metres vrais au centre de la carte,
    sinon un SCR metrique adapte. En cas de doute, celui de la carte (l'outil reste utilisable)."""
    crs = canvas.mapSettings().destinationCrs()
    try:
        center = canvas.extent().center()
        if not crs.isValid() or is_metric(crs, center):
            return crs
        to_wgs84 = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem(WGS84), QgsProject.instance())
        lonlat = to_wgs84.transform(center)
        metric = metric_crs_at(lonlat.x(), lonlat.y())
        return metric if metric.isValid() else crs
    except (QgsCsException, ValueError, RuntimeError):
        return crs


def pixel_size(canvas, crs=None):
    """Taille d'un pixel de la carte exprimee dans `crs`, au centre de la carte : moyenne
    des deux directions (sur une carte en degres, un pixel n'est pas carre en metres).
    Sans `crs` ou dans le SCR de la carte : exactement mapUnitsPerPixel()."""
    upp = canvas.mapUnitsPerPixel()
    dest = canvas.mapSettings().destinationCrs()
    if crs is None or not crs.isValid() or crs == dest:
        return upp
    try:
        xf = QgsCoordinateTransform(dest, crs, QgsProject.instance())
        c = canvas.extent().center()
        o = xf.transform(c)
        ex = xf.transform(QgsPointXY(c.x() + upp, c.y()))
        ey = xf.transform(QgsPointXY(c.x(), c.y() + upp))
    except QgsCsException:
        return upp
    return (G.distance(o, ex) + G.distance(o, ey)) / 2.0 or upp


class Frame:
    """Passages entre le SCR de travail (`crs`), celui de la carte et ceux des couches."""

    def __init__(self, canvas, crs=None):
        self.canvas = canvas
        self.map_crs = canvas.mapSettings().destinationCrs()
        self.crs = crs if crs is not None else work_crs(canvas)
        self.same = self.crs == self.map_crs
        project = QgsProject.instance()
        self._from_map = QgsCoordinateTransform(self.map_crs, self.crs, project)
        self._to_map = QgsCoordinateTransform(self.crs, self.map_crs, project)

    def pixel(self):
        """Taille d'un pixel de la carte, en unites de travail."""
        return pixel_size(self.canvas, self.crs)

    def from_map(self, p):
        """Point de la carte -> SCR de travail."""
        return QgsPointXY(p) if self.same else QgsPointXY(self._from_map.transform(QgsPointXY(p)))

    def to_map(self, p):
        """Point du SCR de travail -> carte."""
        return QgsPointXY(p) if self.same else QgsPointXY(self._to_map.transform(QgsPointXY(p)))

    def geom_to_map(self, geom):
        """Geometrie du SCR de travail -> carte (apercus, surbrillances)."""
        g = QgsGeometry(geom)
        if not self.same:
            g.transform(self._to_map)
        return g

    def from_layer(self, geom, layer):
        """Geometrie d'une couche -> SCR de travail."""
        g = QgsGeometry(geom)
        if layer.crs() != self.crs:
            g.transform(QgsCoordinateTransform(layer.crs(), self.crs, QgsProject.instance()))
        return g

    def to_layer(self, geom, layer):
        """Geometrie du SCR de travail -> couche."""
        g = QgsGeometry(geom)
        if layer.crs() != self.crs:
            g.transform(QgsCoordinateTransform(self.crs, layer.crs(), QgsProject.instance()))
        return g

    def describe(self):
        """« Lambert-93 (EPSG:2154) » : nom court (sans le systeme geodesique), pour que
        le message d'information tienne sur une ligne de la barre de messages."""
        return f"{self.crs.description().split(' / ')[-1]} ({self.crs.authid()})"
