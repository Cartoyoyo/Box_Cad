from qgis.core import QgsVectorLayer

from ._identify_enums import ClickTool


class MapToolPickLayer(ClickTool):
    """Clic sur un objet -> selectionne et met en evidence sa couche
    dans le panneau des couches QGIS. L'outil reste actif en continu."""

    def canvasReleaseEvent(self, event):
        layer, _ = self.top_layer_hit(event)
        if isinstance(layer, QgsVectorLayer):
            self.plugin.pick_layer(layer)
