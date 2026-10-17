from qgis.gui import QgsRubberBand
from qgis.core import (
    QgsVectorLayer, QgsFeature, QgsFeatureRequest, QgsVectorDataProvider,
    QgsProject, QgsWkbTypes, QgsPointXY, QgsGeometry,
    QgsCoordinateTransform, QgsCsException,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QMessageBox

from ._identify_enums import ClickTool

# QGIS 3.30+ a deplace GeometryType dans Qgis.* ; fallback sur l'ancien nom
try:
    from qgis.core import Qgis
    _POLYGON_GEOM = Qgis.GeometryType.Polygon
except AttributeError:
    _POLYGON_GEOM = QgsWkbTypes.PolygonGeometry

DRAG_THRESHOLD = 5  # pixels avant de basculer en mode lasso


def can_delete(layer):
    """Vrai si la couche autorise la suppression d'entites."""
    return (
        isinstance(layer, QgsVectorLayer)
        and not layer.readOnly()
        and bool(layer.dataProvider().capabilities() & QgsVectorDataProvider.Capability.DeleteFeatures)
    )


class MapToolDeleteFeature(ClickTool):
    """Outil suppression avec accumulation de selection :
    - Clic gauche : ajoute l'entite du dessus a la selection (surbrillance).
    - Lasso gauche : selectionne les entites dans le trace, confirmation immediate.
    - Clic droit : demande confirmation puis supprime les entites selectionnees par clic.
    - Echap ou desactivation : annule la selection sans supprimer."""

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self._start_pos = None
        self._rubber_band = None
        self._lasso_points = []              # points carte du lasso en cours
        self._pending = {}                   # dict[layer_id -> [QgsFeature]]

    # ------------------------------------------------------------------
    # Evenements canvas
    # ------------------------------------------------------------------

    def canvasPressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._confirm_delete()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        # Bouton gauche : preparer le lasso libre
        self._cleanup_rubber_band()
        self._start_pos = event.pos()
        self._rubber_band = QgsRubberBand(self.canvas(), _POLYGON_GEOM)
        self._rubber_band.setColor(QColor(220, 50, 50, 50))
        self._rubber_band.setStrokeColor(QColor(200, 0, 0, 200))
        self._rubber_band.setWidth(1)
        self._add_lasso_point(event.pos())

    def canvasMoveEvent(self, event):
        if self._start_pos is None or self._rubber_band is None:
            return
        if not self._is_drag(event.pos()):
            return
        self._add_lasso_point(event.pos())

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._start_pos is None:
            return
        was_drag = self._is_drag(event.pos())
        lasso_pts = list(self._lasso_points)
        try:
            if was_drag:
                self._add_lasso_to_selection(self._build_lasso_geom(lasso_pts))
            else:
                self._add_click_to_selection(event)
        finally:
            self._cleanup_rubber_band()
            self._start_pos = None
        # Apres un lasso, confirmation immediate sans passer par le clic droit
        if was_drag:
            self._confirm_delete()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_selection()
        else:
            super().keyPressEvent(event)

    def _add_lasso_point(self, pos):
        pt = QgsPointXY(self.toMapCoordinates(pos))
        self._lasso_points.append(pt)
        self._rubber_band.addPoint(pt, True)

    def _is_drag(self, pos):
        return (
            self._start_pos is not None
            and (pos - self._start_pos).manhattanLength() >= DRAG_THRESHOLD
        )

    # ------------------------------------------------------------------
    # Construction geometrie lasso
    # ------------------------------------------------------------------

    def _build_lasso_geom(self, points):
        """Construit un polygone ferme (et valide) a partir des points collectes."""
        if len(points) < 3:
            return None
        geom = QgsGeometry.fromPolygonXY([points])
        if not geom.isGeosValid():
            geom = geom.makeValid()   # lasso qui se recoupe
        return geom

    # ------------------------------------------------------------------
    # Accumulation de la selection
    # ------------------------------------------------------------------

    def _add_lasso_to_selection(self, geom):
        """Ajoute toutes les entites intersectant le lasso a la selection en attente."""
        if geom is None or geom.isEmpty():
            return
        canvas_crs = self.canvas().mapSettings().destinationCrs()

        for layer in self.canvas().layers():
            if not can_delete(layer):
                continue
            # Transformer le lasso dans le CRS de la couche si necessaire
            layer_geom = QgsGeometry(geom)
            if canvas_crs != layer.crs():
                xform = QgsCoordinateTransform(canvas_crs, layer.crs(), QgsProject.instance())
                try:
                    layer_geom.transform(xform)
                except QgsCsException:
                    continue
            if layer_geom.isNull() or layer_geom.isEmpty():
                continue

            engine = QgsGeometry.createGeometryEngine(layer_geom.constGet())
            engine.prepareGeometry()
            request = QgsFeatureRequest().setFilterRect(layer_geom.boundingBox())
            features = [
                QgsFeature(f) for f in layer.getFeatures(request)
                if f.hasGeometry() and engine.intersects(f.geometry().constGet())
            ]
            if features:
                self._add_to_pending(layer, features)

    def _add_click_to_selection(self, event):
        """Ajoute l'entite du dessus a la selection en attente."""
        layer, fid = self.top_layer_hit(event)
        if not isinstance(layer, QgsVectorLayer):
            return
        if not can_delete(layer):
            self.plugin.warn(f"La couche « {layer.name()} » n'autorise pas la suppression.")
            return
        feature = layer.getFeature(fid)
        if feature.isValid():
            self._add_to_pending(layer, [QgsFeature(feature)])

    def _add_to_pending(self, layer, features):
        """Merge les nouvelles entites dans _pending et met a jour la surbrillance."""
        pending = self._pending.setdefault(layer.id(), [])
        existing_fids = {f.id() for f in pending}
        new_features = [f for f in features if f.id() not in existing_fids]
        if not new_features:
            return
        pending.extend(new_features)
        layer.selectByIds([f.id() for f in pending])

    # ------------------------------------------------------------------
    # Confirmation et suppression
    # ------------------------------------------------------------------

    def _confirm_delete(self):
        """Affiche un dialogue de confirmation puis supprime si accepte."""
        # Snapshot avant le dialogue ; couches retirees du projet entre-temps ignorees
        project = QgsProject.instance()
        snapshot = {}
        for lid, feats in self._pending.items():
            layer = project.mapLayer(lid)
            if layer is not None:
                snapshot[lid] = (layer, list(feats))
        if not snapshot:
            self._cancel_selection()
            return

        n_total = sum(len(v[1]) for v in snapshot.values())
        n_layers = len(snapshot)
        detail = (
            f"{n_total} entite(s) sur {n_layers} couche(s)"
            if n_layers > 1 else f"{n_total} entite(s)"
        )

        reply = QMessageBox.question(
            self.canvas().window(),
            "Confirmer la suppression",
            f"Supprimer {detail} ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.plugin.delete_features_multi(snapshot)

        self._cancel_selection()

    # ------------------------------------------------------------------
    # Nettoyage
    # ------------------------------------------------------------------

    def _cancel_selection(self):
        """Efface la selection en attente sans supprimer."""
        project = QgsProject.instance()
        for layer_id in self._pending:
            layer = project.mapLayer(layer_id)
            if layer is not None:
                layer.removeSelection()
        self._pending.clear()

    def _cleanup_rubber_band(self):
        if self._rubber_band:
            self.canvas().scene().removeItem(self._rubber_band)
            self._rubber_band = None
        self._lasso_points = []

    def deactivate(self):
        self._cancel_selection()
        self._cleanup_rubber_band()
        self._start_pos = None
        super().deactivate()
