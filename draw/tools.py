"""Outils de dessin : ligne, polyligne, rectangle (2 coins ou oriente), carre, polygone, cercle."""
from qgis.core import QgsPointXY

from . import geometry as G
from . import target as T
from .draw_tool_base import DrawTool

LEN_ANG = [("length", "Longueur", "m"), ("angle", "Angle", "°")]
TYPE_LEN = "tapez la <b>longueur</b> (Tab : <b>angle</b>) puis Entrée"
KEYS_SEGMENTS = "U : annuler le dernier {} · A : angle absolu / relatif · F8 : ortho · F3 : accrochage objet"


class ChainedSegments:
    """Ligne et polyligne : a partir du 2e segment, l'angle tape est une deviation
    par rapport au segment precedent (0 = tout droit) ; A bascule en angle absolu."""

    PROMPT = ""

    def _ref(self):
        if len(self.points) < 2:
            return None
        return G.angle_of(self.points[-2], self.points[-1])

    def _next(self):
        self.configure(self.PROMPT, [("length", "Longueur", "m"), self.angle_field(self._ref() is not None)])

    def on_angle_mode(self):
        key, label, _ = self.angle_field(self._ref() is not None)
        self.dyn.set_label(key, label)

    def _segment(self, mouse):
        base, ref = self.points[-1], self._ref()
        pt, ang, dist = self.constrain_from(base, mouse, ref_angle=ref)
        return base, pt, dist, self.shown_angle(ang, ref)


def _area_info(points):
    if len(points) < 3:
        return ""
    geom = G.polygon_geometry(points)
    return f"Surface {geom.area():.2f} m² · périmètre {geom.length():.2f} m".replace(".", ",")


def _signed(locked, measured):
    """Valeur tapee (positive) avec le signe donne par la souris."""
    if locked is None:
        return measured
    return abs(locked) if measured >= 0 else -abs(locked)


class LineTool(ChainedSegments, DrawTool):
    """LIGNE : segments enchaines, une entite par segment ; Entree/Echap termine."""
    TOOL_NAME = "ligne"
    PROMPT = "Point suivant (Entrée/Échap : fin, U : annuler, A : angle abs./rel.) :"
    GUIDE_STEPS = ("Premier point", "Points suivants")
    GUIDE_HINTS = ("Cliquez le début de la ligne (accrochage objet sur les formes existantes).",
                   f"Cliquez le point suivant, ou {TYPE_LEN}. Chaque segment est une entité ; "
                   "<b>Entrée</b> : terminer.")
    GUIDE_KEYS = KEYS_SEGMENTS.format("segment")

    def start(self):
        self.points = []
        self.configure("LIGNE · premier point :")

    def compute(self, mouse):
        if not self.points:
            return mouse, None, None, {}, ""
        base, pt, dist, shown = self._segment(mouse)
        return pt, [base, pt], "line", {"length": dist, "angle": shown}, ""

    def on_click(self, pt):
        if not self.points:
            self.points = [QgsPointXY(pt)]
            self._next()
            return
        base = self.points[-1]
        if G.distance(base, pt) == 0:
            return
        if self.commit(G.line_geometry([base, pt]), "line"):
            self.points.append(QgsPointXY(pt))
            self._next()

    def undo_point(self):
        if len(self.points) > 1:
            self.undo_last_commit()
            self.points.pop()
            self._next()
        else:
            self.start()


class PolylineTool(ChainedSegments, DrawTool):
    """POLYLIGNE : une seule entite ; Entree/clic droit termine, C ferme."""
    TOOL_NAME = "polyligne"
    PROMPT = "Point suivant (Entrée : fin, C : fermer, U : annuler, A : angle abs./rel.) :"
    GUIDE_STEPS = ("Premier point", "Points suivants")
    GUIDE_HINTS = ("Cliquez le début de la polyligne (accrochage objet sur les formes existantes).",
                   f"Cliquez le point suivant, ou {TYPE_LEN}. <b>Entrée</b> ou clic droit : terminer · "
                   "<b>C</b> : fermer.")
    GUIDE_KEYS = KEYS_SEGMENTS.format("point")

    def start(self):
        self.points = []
        self.configure("POLYLIGNE · premier point :")

    def compute(self, mouse):
        if not self.points:
            return mouse, None, None, {}, ""
        _, pt, dist, shown = self._segment(mouse)
        preview = self.points + [pt]
        total = G.line_geometry(preview).length()
        return pt, preview, "line", {"length": dist, "angle": shown}, f"Longueur totale {total:.2f} m".replace(".", ",")

    def on_click(self, pt):
        if self.points and G.distance(self.points[-1], pt) == 0:
            return
        self.points.append(QgsPointXY(pt))
        self._next()

    def undo_point(self):
        if len(self.points) > 1:
            self.points.pop()
            self._next()
        else:
            self.start()

    def on_finish(self, cancel=False):
        if not cancel and len(self.points) >= 2:
            self.commit(G.line_geometry(self.points), "line")
        self.start()

    def on_close(self):
        if len(self.points) < 3:
            self.plugin.warn("Il faut au moins 3 points pour fermer la polyligne.")
            return
        # Fermee : polygone si la couche active est une couche polygone en edition,
        # sinon ligne fermee (comme une polyligne fermee en DAO)
        if T.active_target(self.plugin.iface, "polygon") is not None:
            self.commit(G.polygon_geometry(self.points), "polygon")
        else:
            self.commit(G.line_geometry(self.points, closed=True), "line")
        self.start()


class RectangleTool(DrawTool):
    """RECTANG : 2 coins opposes (largeur x hauteur), ou oriente
    (premier cote longueur + angle, puis hauteur)."""

    def __init__(self, canvas, plugin, action, oriented=False):
        super().__init__(canvas, plugin, action)
        self.oriented = oriented
        self.TOOL_NAME = "rectangle orienté" if oriented else "rectangle"
        self.side_angle = 0.0
        if oriented:
            self.GUIDE_STEPS = ("Premier coin", "Premier côté", "Hauteur")
            self.GUIDE_HINTS = ("Cliquez le premier coin du rectangle.",
                                f"Cliquez la fin du premier côté, ou {TYPE_LEN}.",
                                "Placez la souris du côté voulu et cliquez, ou tapez la <b>hauteur</b> puis Entrée.")
        else:
            self.GUIDE_STEPS = ("Premier coin", "Coin opposé")
            self.GUIDE_HINTS = ("Cliquez un coin du rectangle.",
                                "Cliquez le coin opposé, ou tapez la <b>largeur</b> (Tab : <b>hauteur</b>) puis "
                                "Entrée ; la souris donne le côté.")

    def start(self):
        self.points = []
        self.configure(("RECTANGLE ORIENTÉ" if self.oriented else "RECTANGLE") + " · premier coin :")

    def compute(self, mouse):
        if not self.points:
            return mouse, None, None, {}, ""
        p1 = self.points[0]
        if not self.oriented:
            dx, dy = mouse.x() - p1.x(), mouse.y() - p1.y()
            if not (self.snapped and not self.dyn.has_input()):
                dx = _signed(self.dyn.locked("width"), dx)
                dy = _signed(self.dyn.locked("height"), dy)
            c2 = QgsPointXY(p1.x() + dx, p1.y() + dy)
            pts = G.rect_two_corners(p1, c2)
            return c2, pts, "polygon", {"width": abs(dx), "height": abs(dy)}, _area_info(pts)
        if len(self.points) == 1:
            pt, ang, dist = self.constrain_from(p1, mouse)
            return pt, [p1, pt], "line", {"length": dist, "angle": ang}, ""
        length = G.distance(p1, self.points[1])
        h = _signed(self.dyn.locked("height"), G.signed_offset(p1, self.side_angle, mouse))
        pts = G.rect_oriented(p1, self.side_angle, length, h)
        return pts[2], pts, "polygon", {"height": abs(h)}, _area_info(pts)

    def on_click(self, pt):
        if not self.points:
            self.points = [QgsPointXY(pt)]
            if self.oriented:
                self.configure("Premier côté (longueur, angle) :", LEN_ANG)
            else:
                self.configure("Coin opposé (largeur, hauteur) :",
                               [("width", "Largeur", "m"), ("height", "Hauteur", "m")])
            return
        p1 = self.points[0]
        if self.oriented and len(self.points) == 1:
            if G.distance(p1, pt) == 0:
                return
            self.side_angle = G.angle_of(p1, pt)
            self.points.append(QgsPointXY(pt))
            self.configure("Hauteur (côté indiqué par la souris) :", [("height", "Hauteur", "m")])
            return
        _, pts, _, _, _ = self.compute(self.mouse if self.mouse is not None else pt)
        if G.polygon_geometry(pts).area() > 0:
            self.commit(G.polygon_geometry(pts), "polygon")
        self.start()


class SquareTool(DrawTool):
    """CARRÉ : coin, puis premier côté (côté, angle) ; le carré est construit
    à gauche de ce côté (sens anti-horaire)."""
    TOOL_NAME = "carré"
    GUIDE_STEPS = ("Premier coin", "Côté")
    GUIDE_HINTS = ("Cliquez un coin du carré.",
                   "Cliquez la fin du côté, ou tapez le <b>côté</b> (Tab : <b>angle</b>) puis Entrée ; "
                   "le carré se construit à gauche du côté.")

    def start(self):
        self.points = []
        self.configure("CARRÉ · premier coin :")

    def compute(self, mouse):
        if not self.points:
            return mouse, None, None, {}, ""
        p1 = self.points[0]
        pt, ang, side = self.constrain_from(p1, mouse, length_key="side")
        pts = G.square(p1, side, ang)
        return pt, pts, "polygon", {"side": side, "angle": ang}, _area_info(pts)

    def on_click(self, pt):
        if not self.points:
            self.points = [QgsPointXY(pt)]
            self.configure("Côté (longueur, angle) :", [("side", "Côté", "m"), ("angle", "Angle", "°")])
            return
        _, pts, _, _, _ = self.compute(self.mouse if self.mouse is not None else pt)
        if G.polygon_geometry(pts).area() > 0:
            self.commit(G.polygon_geometry(pts), "polygon")
        self.start()


class CircleTool(DrawTool):
    """CERCLE : centre, puis rayon."""
    TOOL_NAME = "cercle"
    GUIDE_STEPS = ("Centre", "Rayon")
    GUIDE_HINTS = ("Cliquez le centre du cercle (accrochage objet sur les formes existantes).",
                   "Cliquez un point du cercle, ou tapez le <b>rayon</b> puis Entrée.")

    def start(self):
        self.points = []
        self.configure("CERCLE · centre :")

    def compute(self, mouse):
        if not self.points:
            return mouse, None, None, {}, ""
        center = self.points[0]
        r = self.dyn.locked("radius")
        r = abs(r) if r is not None else G.distance(center, mouse)
        pts = G.circle(center, r) if r > 0 else []
        info = f"Diamètre {2 * r:.2f} m · surface {G.polygon_geometry(pts).area():.2f} m²".replace(".", ",") if pts else ""
        return G.polar_point(center, r, 0), pts, "polygon", {"radius": r}, info

    def on_click(self, pt):
        if not self.points:
            self.points = [QgsPointXY(pt)]
            self.configure("Rayon :", [("radius", "Rayon", "m")])
            return
        _, pts, _, _, _ = self.compute(self.mouse if self.mouse is not None else pt)
        if pts:
            self.commit(G.polygon_geometry(pts), "polygon")
        self.start()


class PolygonTool(ChainedSegments, DrawTool):
    """POLYGONE : un clic par sommet ; Entree, clic droit ou C ferme (3 sommets minimum)."""
    TOOL_NAME = "polygone"
    PROMPT = "Sommet suivant (Entrée / C : fermer, U : annuler, A : angle abs./rel.) :"
    GUIDE_STEPS = ("Premier sommet", "Sommets suivants")
    GUIDE_HINTS = ("Cliquez le premier sommet du polygone.",
                   f"Cliquez le sommet suivant, ou {TYPE_LEN}. <b>Entrée</b>, clic droit ou <b>C</b> : fermer "
                   "(3 sommets minimum).")
    GUIDE_KEYS = KEYS_SEGMENTS.format("sommet")

    def start(self):
        self.points = []
        self.configure("POLYGONE · premier sommet :")

    def compute(self, mouse):
        if not self.points:
            return mouse, None, None, {}, ""
        _, pt, dist, shown = self._segment(mouse)
        preview = self.points + [pt]
        kind = "polygon" if len(preview) >= 3 else "line"
        return pt, preview, kind, {"length": dist, "angle": shown}, _area_info(preview)

    def on_click(self, pt):
        if self.points and G.distance(self.points[-1], pt) == 0:
            return
        self.points.append(QgsPointXY(pt))
        self._next()

    def undo_point(self):
        if len(self.points) > 1:
            self.points.pop()
            self._next()
        else:
            self.start()

    def on_finish(self, cancel=False):
        if not cancel:
            self.on_close()
            return
        self.start()

    def on_close(self):
        if len(self.points) < 3:
            self.plugin.warn("Il faut au moins 3 sommets pour fermer le polygone.")
            return
        geom = G.polygon_geometry(self.points)
        if geom.area() > 0:
            self.commit(geom, "polygon")
        self.start()
