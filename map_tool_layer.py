from qgis.core import QgsVectorLayer

from ._identify_enums import ClickTool


class MapToolHideLayer(ClickTool):
    """Clic sur un objet -> masque sa couche entiere."""

    def canvasReleaseEvent(self, event):
        layer, _ = self.top_layer_hit(event)
        if isinstance(layer, QgsVectorLayer):
            self.plugin.hide_layer(layer)
