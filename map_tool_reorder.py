from qgis.core import QgsVectorLayer

from ._identify_enums import ClickTool


class MapToolReorderLayer(ClickTool):
    """Clic sur un objet -> deplace sa couche (top/up/down/bottom)."""

    def __init__(self, canvas, plugin, action, position):
        super().__init__(canvas, plugin, action)
        self.position = position  # 'top' | 'up' | 'down' | 'bottom'

    def canvasReleaseEvent(self, event):
        layer, _ = self.top_layer_hit(event)
        if isinstance(layer, QgsVectorLayer):
            self.plugin._reorder_layer_by_layer(layer, self.position)
