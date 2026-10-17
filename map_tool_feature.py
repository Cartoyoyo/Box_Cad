from qgis.core import QgsVectorLayer

from ._identify_enums import ClickTool


class MapToolHideFeature(ClickTool):
    """Clic sur un objet -> masque cette entite via subsetString."""

    def canvasReleaseEvent(self, event):
        layer, fid = self.top_layer_hit(event)
        if isinstance(layer, QgsVectorLayer):
            self.plugin.hide_feature(layer, fid)
