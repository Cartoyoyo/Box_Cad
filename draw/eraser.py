"""GOMME : efface les formes des couches brouillon de dessin (jamais les donnees metier).

- survol : la forme sous le curseur est surlignee en rouge ;
- clic : efface cette forme ;
- cliquer-glisser : lasso, efface toutes les formes qu'il touche ;
- le bouton Annuler de Box_cad restaure le dernier effacement.
"""
from qgis.core import QgsGeometry, QgsPointXY
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from . import target as T
from .crs import pixel_size
from .guide import GuideBanner, guide_html
from .pick import draft_layers, features_in_lasso, hit_geometry, pick_feature

DRAG_THRESHOLD = 5      # pixels avant de passer en lasso
GUIDE = ("Gomme", ("Formes à effacer",), 0,
         "Cliquez une forme dessinée pour l'effacer, ou <b>cliquer-glisser</b> autour de plusieurs formes.",
         "Couches brouillon seulement, jamais vos données · bouton Annuler de Box_cad : restaurer")


class EraserTool(QgsMapTool):
    def __init__(self, canvas, plugin, action):
        super().__init__(canvas)
        self.plugin = plugin
        self.action = action
        self._hover = None          # (layer, feature) sous le curseur
        self._start_pos = None
        self._dragged = False       # la souris s'est eloignee du clic : c'est un lasso
        self._lasso = []
        self.rb_hover = self.rb_lasso = None
        self.banner = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def activate(self):
        super().activate()
        self.rb_hover = QgsRubberBand(self.canvas(), T.POLYGON)
        self.rb_hover.setStrokeColor(QColor(220, 38, 38))
        self.rb_hover.setFillColor(QColor(220, 38, 38, 60))
        self.rb_hover.setWidth(3)
        self.rb_lasso = QgsRubberBand(self.canvas(), T.POLYGON)
        self.rb_lasso.setStrokeColor(QColor(200, 0, 0, 200))
        self.rb_lasso.setFillColor(QColor(220, 50, 50, 40))
        self.rb_lasso.setWidth(1)
        self.banner = GuideBanner(self.plugin.iface)
        self.update_guide()
        if not draft_layers():
            self.plugin.iface.messageBar().pushInfo(
                "Box_cad", "Gomme : aucune couche brouillon de dessin pour l'instant.")

    def deactivate(self):
        for rb in (self.rb_hover, self.rb_lasso):
            if rb is not None:
                self.canvas().scene().removeItem(rb)
        self.rb_hover = self.rb_lasso = None
        if self.banner is not None:
            self.banner.hide()
            self.banner = None
        self._hover = None
        self._start_pos = None
        super().deactivate()
        self.plugin._on_tool_deactivated(self.action)

    def update_guide(self):
        """Bandeau d'aide (menu Modes de dessin → Bandeau d'aide)."""
        if self.banner is None:
            return
        if getattr(self.plugin, "draw_guide", True):
            self.banner.show(guide_html(*GUIDE))
        else:
            self.banner.hide()

    # ------------------------------------------------------------------
    # Souris
    # ------------------------------------------------------------------

    def canvasPressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._start_pos = event.pos()
        self._dragged = False
        self._lasso = [QgsPointXY(event.mapPoint())]
        self.rb_lasso.reset(T.POLYGON)

    def canvasMoveEvent(self, event):
        if self.rb_hover is None:
            return
        if self._start_pos is not None and self._is_drag(event.pos()):
            self._lasso.append(QgsPointXY(event.mapPoint()))
            if self.rb_lasso.numberOfVertices() == 0:
                for pt in self._lasso:
                    self.rb_lasso.addPoint(pt, True)
            else:
                self.rb_lasso.addPoint(self._lasso[-1], True)
            self._set_hover(None)
            return
        self._set_hover(self._pick(event.mapPoint()))

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._start_pos is None:
            return
        was_drag = self._is_drag(event.pos())     # vrai meme relache pres du depart
        pts = list(self._lasso)
        self._start_pos = None
        self._lasso = []
        self.rb_lasso.reset(T.POLYGON)
        if was_drag:
            if len(pts) >= 3:
                geom = QgsGeometry.fromPolygonXY([pts + [pts[0]]])
                if not geom.isGeosValid():
                    geom = geom.makeValid()
                self._erase(self._in_lasso(geom))
        else:
            hit = self._pick(event.mapPoint())
            if hit:
                self._erase({hit[0].id(): (hit[0], [hit[1]])})
        self._set_hover(self._pick(event.mapPoint()))

    def _is_drag(self, pos):
        """Une fois le seuil franchi pendant l'appui, on reste en lasso jusqu'au relachement."""
        if not self._dragged:
            self._dragged = (pos - self._start_pos).manhattanLength() >= DRAG_THRESHOLD
        return self._dragged

    # ------------------------------------------------------------------
    # Recherche des formes
    # ------------------------------------------------------------------

    def _pick(self, map_point):
        return pick_feature(self.canvas(), draft_layers(), map_point)

    def _in_lasso(self, lasso):
        return features_in_lasso(self.canvas(), draft_layers(), lasso)

    def _set_hover(self, hit):
        self._hover = hit
        if self.rb_hover is None:
            return
        self.rb_hover.reset(T.POLYGON)
        if hit:
            layer, feature = hit
            geom = hit_geometry(self.canvas(), layer, feature)
            if geom.type() != T.POLYGON:
                geom = geom.buffer(pixel_size(self.canvas(), layer.crs()) * 2, 4)
            self.rb_hover.setToGeometry(geom, layer)

    # ------------------------------------------------------------------
    # Effacement
    # ------------------------------------------------------------------

    def _erase(self, found):
        """found : {layer_id: (layer, [QgsFeature])}. Effacement direct, annulable."""
        saved = {}
        for layer_id, (layer, feats) in found.items():
            if layer.dataProvider().deleteFeatures([f.id() for f in feats]):
                saved[layer_id] = feats
                layer.triggerRepaint()
        if saved:
            n = sum(len(v) for v in saved.values())
            self.plugin._set_undo(('draft_erase', saved))
            self.plugin.iface.messageBar().pushSuccess(
                "Box_cad", f"{n} forme(s) effacée(s) — bouton Annuler pour les restaurer.")
