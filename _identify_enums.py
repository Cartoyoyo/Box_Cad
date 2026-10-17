"""Enums d'identification compatibles QGIS 3.16 -> 3.36+ (deplaces dans Qgis.*)."""
from qgis.gui import QgsMapToolIdentify

try:
    from qgis.core import Qgis
    IDENTIFY_TOPDOWN = Qgis.IdentifyMode.TopDownStopAtFirst
    LAYER_VECTOR = Qgis.LayerType.Vector
except AttributeError:
    IDENTIFY_TOPDOWN = QgsMapToolIdentify.IdentifyMode.TopDownStopAtFirst
    LAYER_VECTOR = QgsMapToolIdentify.Type.VectorLayer


class ClickTool(QgsMapToolIdentify):
    """Base des outils 'clic sur un objet'.
    QGIS appelle deactivate() (methode virtuelle) quand un autre outil prend la main ;
    deactivated est un signal, le surcharger en Python n'est jamais appele."""

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas)
        self.plugin = plugin
        self.action = action

    def top_layer_hit(self, event):
        """Retourne (couche, fid) de l'objet vecteur du dessus, ou (None, None)."""
        results = self.identify(event.x(), event.y(), IDENTIFY_TOPDOWN, LAYER_VECTOR)
        if results:
            return results[0].mLayer, results[0].mFeature.id()
        return None, None

    def deactivate(self):
        super().deactivate()
        self.plugin._on_tool_deactivated(self.action)
