"""Transformations facon AutoCAD sur UN objet : Deplacer, Copier, Rotation, Miroir, Echelle.

Etapes communes : designer l'objet (surbrillance au survol) -> point(s) -> valeur.
Objets concernes : dessins Box_cad (brouillons visibles) et couche active en edition.
  - brouillon : changement direct + bouton Annuler de Box_cad ;
  - couche active : commande d'edition QGIS (Ctrl+Z), rien n'est enregistre sur disque.
Cotes associatives : Deplacer, Rotation, Miroir et Echelle d'une forme entrainent les cotes
et les angles poses sur elle (points mesures sur la forme), annules avec elle.
Deplacer une cote ou un angle ne bouge que la ligne de cote / l'arc : les rappels s'adaptent.
"""
from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsProject,
    QgsVectorLayerUtils,
)
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtGui import QColor

from . import geometry as G
from . import target as T
from .angle_dim import angle_parts, build_angle, moved_angle
from .dimension import dimension_geometry, dimension_parts, moved_dimension
from .draw_tool_base import DrawTool
from .pick import _number, editable_layers, hit_geometry, is_text_layer, pick_feature, to_layer

STEP_PICK = 0
LINKED_KINDS = ("dimension", "angle")
LINK_TOLERANCE_PX = 0.5     # point de cote considere comme pose sur la forme


def _kind(layer):
    return layer.customProperty(T.DRAFT_PROPERTY)


class TransformTool(DrawTool):
    """Base : les sous-classes definissent NAME, after_pick(), compute_step(mouse),
    click_step(pt), finish_step() et transform_geometry(layer, feature)."""

    NAME = ""

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.TOOL_NAME = self.NAME.lower()
        self.step = STEP_PICK
        self.target = None      # (couche, QgsFeature) designe
        self.hover = None
        self.base = None        # point de base, SCR du canevas
        self.rb_obj = None

    def activate(self):
        super().activate()
        self.rb_obj = QgsRubberBand(self.canvas(), T.POLYGON)
        self.rb_obj.setStrokeColor(QColor(37, 99, 235))
        self.rb_obj.setFillColor(QColor(37, 99, 235, 40))
        self.rb_obj.setWidth(2)

    def deactivate(self):
        if self.rb_obj is not None:
            self.canvas().scene().removeItem(self.rb_obj)
            self.rb_obj = None
        super().deactivate()

    def wants_osnap(self):
        return self.step != STEP_PICK      # pas d'accrochage pour designer l'objet

    # ------------------------------------------------------------------
    # Etapes communes
    # ------------------------------------------------------------------

    def start(self):
        self.step = STEP_PICK
        self.target = self.hover = self.base = None
        self.points = []
        self.configure(f"{self.NAME.upper()} · cliquez l'objet :")

    def compute(self, mouse):
        if self.step == STEP_PICK:
            self.hover = pick_feature(self.canvas(), editable_layers(self.plugin.iface), mouse)
            return mouse, None, None, {}, ""
        return self.compute_step(mouse)

    def on_click(self, pt):
        if self.step == STEP_PICK:
            if self.hover is None:
                self.plugin.warn(f"{self.NAME} : cliquez une forme dessinée, "
                                 "ou un objet de la couche active en édition.")
                return
            self.target = self.hover
            self.after_pick()
        else:
            self.click_step(QgsPointXY(pt))

    def on_finish(self, cancel=False):
        if cancel or self.step == STEP_PICK:
            self.start()
        else:
            self.finish_step()

    def undo_point(self):
        self.start()

    def draw_extra(self):
        if self.rb_obj is None:
            return
        self.rb_obj.reset(T.POLYGON)
        hit = self.target or self.hover
        if not hit:
            return
        layer, feature = hit
        geom, attrs = None, {}
        if self.step != STEP_PICK:
            geom = self.transform_geometry(layer, feature)
            if geom is not None:
                attrs = self.transform_attrs(layer, feature)
        # un texte est montre par son cadre (position, angle et taille apres transformation)
        geom = hit_geometry(self.canvas(), layer, feature, geom, attrs.get("angle"), attrs.get("taille"))
        if geom.type() != T.POLYGON:
            geom = geom.buffer(self.canvas().mapUnitsPerPixel() * 2, 4)
        self.rb_obj.setToGeometry(geom, layer)

    # ------------------------------------------------------------------
    # Outils communs
    # ------------------------------------------------------------------

    def layer_point(self, pt, layer):
        """Point du canevas exprime dans le SCR de la couche."""
        return to_layer(QgsGeometry.fromPointXY(QgsPointXY(pt)), self.canvas(), layer).asPoint()

    def center_of(self, layer, feature):
        """Centre de l'objet (d'un texte : centre de son cadre), SCR du canevas."""
        c = hit_geometry(self.canvas(), layer, feature).centroid().asPoint()
        return QgsPointXY(self.canvas().mapSettings().layerToMapCoordinates(layer, c))

    def transform_attrs(self, layer, feature):
        """Attributs a modifier avec la geometrie (angle / taille d'un texte)."""
        return {}

    def qtransform(self, layer):
        """Transformation en cours dans le SCR de `layer` (QTransform), ou None.
        Sert a entrainer les cotes associees ; les outils qui n'en ont pas n'entrainent rien."""
        return None

    @staticmethod
    def _change_draft(layer, feature, new_geom, attrs):
        """Modifie un objet de brouillon ; retourne l'element d'annulation ou None."""
        old_geom = QgsGeometry(feature.geometry())
        changes, old = {}, {}
        for name, value in (attrs or {}).items():
            idx = layer.fields().indexOf(name)
            if idx >= 0:
                changes[idx] = value
                old[idx] = feature.attribute(idx)
        provider = layer.dataProvider()
        if not provider.changeGeometryValues({feature.id(): new_geom}):
            return None
        if changes:
            provider.changeAttributeValues({feature.id(): changes})
        layer.triggerRepaint()
        return (layer.id(), feature.id(), old_geom, old)

    def replace_geometry(self, new_geom, attrs=None, follow=False):
        """Remplace la geometrie (et d'eventuels attributs) de l'objet designe (annulable).
        `follow` : les cotes et angles poses sur l'objet suivent (meme annulation)."""
        layer, feature = self.target
        linked = self.linked_annotations(layer, feature) if follow and _kind(layer) not in LINKED_KINDS else []
        items = []
        if layer.customProperty(T.DRAFT_PROPERTY):
            item = self._change_draft(layer, feature, new_geom, attrs)
            if item is None:
                self.plugin.warn(f"{self.NAME} refusé par « {layer.name()} ».")
                return False
            items.append(item)
        else:
            layer.beginEditCommand(f"{self.NAME} [Box_cad]")
            if not layer.changeGeometry(feature.id(), new_geom):
                layer.destroyEditCommand()
                self.plugin.warn(f"{self.NAME} refusé par « {layer.name()} ».")
                return False
            layer.endEditCommand()
            layer.triggerRepaint()
        for ann_layer, ann_feature, ann_geom, ann_attrs in linked:
            item = self._change_draft(ann_layer, ann_feature, ann_geom, ann_attrs)
            if item is not None:
                items.append(item)
        if len(items) == 1 and layer.customProperty(T.DRAFT_PROPERTY):
            self.plugin._set_undo(('draft_geom',) + items[0])
        elif items:
            self.plugin._set_undo(('draft_geoms', items))
        return True

    def linked_annotations(self, layer, feature):
        """Cotes et angles dont les points mesures sont sur l'objet : [(couche, objet,
        nouvelle geometrie, attributs)]. Les deux points sur l'objet : la cote suit en bloc ;
        un seul : ce point suit, la cote garde son decalage. Angle : son sommet (ou ses
        deux cotes) sur l'objet, il suit en bloc."""
        result = []
        project = QgsProject.instance()
        old = QgsGeometry(feature.geometry())
        tol = LINK_TOLERANCE_PX * self.canvas().mapUnitsPerPixel()
        for ann in project.mapLayers().values():
            kind = _kind(ann)
            if kind not in LINKED_KINDS or ann is layer:
                continue
            q = self.qtransform(ann)
            if q is None:
                continue
            shape = QgsGeometry(old)
            if layer.crs() != ann.crs():
                shape.transform(QgsCoordinateTransform(layer.crs(), ann.crs(), project))

            def on(p, shape=shape):
                return shape.distance(QgsGeometry.fromPointXY(p)) <= tol

            request = QgsFeatureRequest().setFilterRect(shape.boundingBox().buffered(tol))
            for f in ann.getFeatures(request):
                g = f.geometry()
                if kind == "dimension":
                    parts = dimension_parts(g)
                    if parts is None:
                        continue
                    p1, p2, offset = parts
                    on1, on2 = on(p1), on(p2)
                    if not (on1 or on2):
                        continue
                    n1 = G.map_point(q, p1) if on1 else p1
                    n2 = G.map_point(q, p2) if on2 else p2
                    if G.distance(n1, n2) == 0:
                        continue
                    new = G.transformed(g, q) if on1 and on2 else dimension_geometry(n1, n2, offset)[0]
                    result.append((ann, QgsFeature(f), new, {"longueur": round(G.distance(n1, n2), 2)}))
                else:
                    parts = angle_parts(g)
                    if parts is None:
                        continue
                    vertex, start, sweep, r, reaches = parts
                    sides = [G.polar_point(vertex, reach, d) for reach, d in zip(reaches, (start, start + sweep))]
                    if not (on(vertex) or all(on(p) for p in sides)):
                        continue
                    new = _reoriented_angle(G.transformed(g, q))
                    new_parts = angle_parts(new)
                    attrs = {"longueur": round(new_parts[3], 2)} if new_parts else {}
                    result.append((ann, QgsFeature(f), new, attrs))
        return result

    # a definir par les sous-classes
    def after_pick(self):
        raise NotImplementedError

    def compute_step(self, mouse):
        raise NotImplementedError

    def click_step(self, pt):
        raise NotImplementedError

    def finish_step(self):
        self.start()

    def transform_geometry(self, layer, feature):
        return None


# ----------------------------------------------------------------------
# Deplacer / Copier
# ----------------------------------------------------------------------

class MoveTool(TransformTool):
    """DEPLACER : objet -> point de base -> point d'arrivee (longueur, angle)."""
    NAME = "Déplacer"
    STEP_BASE, STEP_DEST = 1, 2
    dest = None

    def after_pick(self):
        self.step = self.STEP_BASE
        self.configure("Point de base :")

    def compute_step(self, mouse):
        if self.step == self.STEP_BASE:
            self.dest = None
            return mouse, None, None, {}, ""
        pt, ang, dist = self.constrain_from(self.base, mouse)
        self.dest = pt
        return pt, [self.base, pt], "line", {"length": dist, "angle": ang}, ""

    def click_step(self, pt):
        if self.step == self.STEP_BASE:
            self.base = pt
            self.points = [pt]          # point precedent pour l'accrochage (perpendiculaire...)
            self.step = self.STEP_DEST
            self.configure("Point d'arrivée (longueur, angle) :",
                           [("length", "Longueur", "m"), ("angle", "Angle", "°")])
        else:
            self.dest = pt
            self.place()

    def finish_step(self):
        if self.step == self.STEP_DEST and self.dest is not None and self.dyn.has_input():
            self.place()
        else:
            self.start()

    def offset_geometry(self, layer, feature):
        if self.base is None or getattr(self, "dest", None) is None:
            return None
        b, d = self.layer_point(self.base, layer), self.layer_point(self.dest, layer)
        geom = QgsGeometry(feature.geometry())
        geom.translate(d.x() - b.x(), d.y() - b.y())
        return geom

    def qtransform(self, layer):
        if self.base is None or getattr(self, "dest", None) is None:
            return None
        b, d = self.layer_point(self.base, layer), self.layer_point(self.dest, layer)
        return G.translate_transform(d.x() - b.x(), d.y() - b.y())

    def move_geometry(self, layer, feature):
        """Une cote ou un angle : seule la ligne de cote / l'arc se deplace."""
        q = self.qtransform(layer)
        if q is None:
            return None
        if _kind(layer) == "dimension":
            return moved_dimension(feature.geometry(), q.dx(), q.dy())
        if _kind(layer) == "angle":
            return moved_angle(feature.geometry(), q.dx(), q.dy())
        return self.offset_geometry(layer, feature)

    def transform_geometry(self, layer, feature):
        return self.move_geometry(layer, feature) if self.step == self.STEP_DEST else None

    def transform_attrs(self, layer, feature):
        if _kind(layer) == "angle" and self.step == self.STEP_DEST:
            parts = angle_parts(self.move_geometry(layer, feature))
            return {"longueur": round(parts[3], 2)} if parts else {}
        return {}

    def place(self):
        geom = self.move_geometry(*self.target)
        if geom is not None:
            self.replace_geometry(geom, self.transform_attrs(*self.target), follow=True)
        self.start()


class CopyTool(MoveTool):
    """COPIER : objet -> point de base -> chaque clic pose une copie ; Entree/Echap termine."""
    NAME = "Copier"

    def transform_geometry(self, layer, feature):
        # une copie est toujours un double complet (cote comprise), pose au point d'arrivee
        return self.offset_geometry(layer, feature) if self.step == self.STEP_DEST else None

    def transform_attrs(self, layer, feature):
        return {}

    def finish_step(self):
        if self.step == self.STEP_DEST and self.dest is not None and self.dyn.has_input():
            self.place()                # valeur tapee + Entree : une copie de plus
        else:
            self.start()

    def place(self):
        layer, feature = self.target
        geom = self.offset_geometry(layer, feature)
        if geom is None:
            return
        if layer.customProperty(T.DRAFT_PROPERTY):
            copy = QgsFeature(feature)
            copy.setGeometry(geom)
            ok, added = layer.dataProvider().addFeatures([copy])
            if ok and added:
                self.plugin._set_undo(('add_features', layer.id(), [added[0].id()]))
                layer.triggerRepaint()
        else:
            # createFeature : attributs recopies SAUF la cle primaire (fid GPKG...), valeurs
            # par defaut et contraintes d'unicite gerees ; pas de copie des objets lies par
            # relation (duplicateFeature les recopierait sans limite de profondeur)
            pk = set(layer.primaryKeyAttributes())
            attrs = {i: v for i, v in enumerate(feature.attributes()) if i not in pk}
            new = QgsVectorLayerUtils.createFeature(layer, geom, attrs, layer.createExpressionContext())
            layer.beginEditCommand("Copier [Box_cad]")
            if layer.addFeature(new):
                layer.endEditCommand()
                layer.triggerRepaint()
            else:
                layer.destroyEditCommand()
                self.plugin.warn(f"Copie refusée par « {layer.name()} ».")
        # on reste a l'etape « point d'arrivee » pour enchainer les copies
        self.configure("Copie suivante (Entrée/Échap : fin) :",
                       [("length", "Longueur", "m"), ("angle", "Angle", "°")])


# ----------------------------------------------------------------------
# Rotation
# ----------------------------------------------------------------------

class RotateTool(TransformTool):
    """ROTATION : objet -> point de base (Entree : centre de l'objet) -> angle anti-horaire."""
    NAME = "Rotation"
    STEP_BASE, STEP_ANGLE = 1, 2

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.angle = 0.0

    def start(self):
        super().start()
        self.angle = 0.0

    def after_pick(self):
        self.step = self.STEP_BASE
        self.configure("Point de base (Entrée : centre de l'objet) :")

    def _ask_angle(self):
        self.step = self.STEP_ANGLE
        self.points = [self.base]
        self.configure("Angle de rotation (anti-horaire) :", [("angle", "Angle", "°")])

    def compute_step(self, mouse):
        if self.step == self.STEP_BASE:
            return mouse, None, None, {}, ""
        _, ang, _ = self.constrain_from(self.base, mouse, length_key="", angle_key="angle")
        self.angle = ang
        far = G.polar_point(self.base, G.distance(self.base, mouse) or self.canvas().mapUnitsPerPixel() * 40, ang)
        return mouse, [self.base, far], "line", {"angle": ang}, ""

    def click_step(self, pt):
        if self.step == self.STEP_BASE:
            self.base = pt
            self._ask_angle()
        else:
            self._apply()

    def finish_step(self):
        if self.step == self.STEP_BASE:      # Entree : centre de l'objet comme point de base
            self.base = self.center_of(*self.target)
            self._ask_angle()
        else:
            self._apply()

    def undo_point(self):
        if self.step == self.STEP_ANGLE:
            self.after_pick()
        else:
            self.start()

    def qtransform(self, layer):
        if self.step != self.STEP_ANGLE or self.base is None:
            return None
        return G.rotate_transform(self.layer_point(self.base, layer), self.angle)

    def transform_geometry(self, layer, feature):
        q = self.qtransform(layer)
        return G.transformed(feature.geometry(), q) if q is not None else None

    def transform_attrs(self, layer, feature):
        if not is_text_layer(layer):
            return {}
        return {"angle": round((_number(feature["angle"], 0.0) + self.angle) % 360.0, 2)}

    def _apply(self):
        self.replace_geometry(self.transform_geometry(*self.target), self.transform_attrs(*self.target),
                              follow=True)
        self.start()


# ----------------------------------------------------------------------
# Miroir
# ----------------------------------------------------------------------

class MirrorTool(TransformTool):
    """MIROIR : objet -> 1er point de l'axe -> 2e point (angle) ; objet retourne sur place."""
    NAME = "Miroir"
    STEP_P1, STEP_P2 = 1, 2

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.axis_angle = None

    def after_pick(self):
        self.step = self.STEP_P1
        self.configure("Premier point de l'axe de symétrie :")

    def compute_step(self, mouse):
        if self.step == self.STEP_P1:
            self.axis_angle = None
            return mouse, None, None, {}, ""
        pt, ang, dist = self.constrain_from(self.base, mouse, length_key="", angle_key="angle")
        self.axis_angle = ang
        far = self.canvas().mapUnitsPerPixel() * 2000
        return pt, [G.polar_point(self.base, far, ang + 180), G.polar_point(self.base, far, ang)], \
            "line", {"angle": ang}, ""

    def click_step(self, pt):
        if self.step == self.STEP_P1:
            self.base = pt
            self.points = [pt]
            self.step = self.STEP_P2
            self.configure("Second point de l'axe (angle) :", [("angle", "Angle", "°")])
        else:
            self._apply()

    def finish_step(self):
        if self.step == self.STEP_P2 and self.axis_angle is not None:
            self._apply()
        else:
            self.start()

    def qtransform(self, layer):
        if self.step != self.STEP_P2 or self.axis_angle is None:
            return None
        return G.mirror_transform(self.layer_point(self.base, layer), self.axis_angle)

    def transform_geometry(self, layer, feature):
        q = self.qtransform(layer)
        if q is None:
            return None
        geom = G.transformed(feature.geometry(), q)
        return _reoriented_angle(geom) if _kind(layer) == "angle" else geom

    def transform_attrs(self, layer, feature):
        """Texte : orientation symetrisee mais toujours lisible (jamais a l'envers)."""
        if not is_text_layer(layer) or self.axis_angle is None:
            return {}
        a = (2.0 * self.axis_angle - _number(feature["angle"], 0.0)) % 360.0
        if 90.0 < a <= 270.0:
            a = (a + 180.0) % 360.0
        return {"angle": round(a, 2)}

    def _apply(self):
        geom = self.transform_geometry(*self.target)
        if geom is not None:
            self.replace_geometry(geom, self.transform_attrs(*self.target), follow=True)
        self.start()


# ----------------------------------------------------------------------
# Echelle
# ----------------------------------------------------------------------

class ScaleTool(TransformTool):
    """ECHELLE : objet -> point de base -> facteur (tape, ou souris : distance au point
    de base rapportee a la demi-taille de l'objet). R : mode Reference (longueur
    actuelle -> nouvelle longueur)."""
    NAME = "Échelle"
    STEP_BASE, STEP_FACTOR = 1, 2
    HANDLED_EXTRA = "R"

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas, plugin, action)
        self.factor = 1.0
        self.reference = False

    def start(self):
        super().start()
        self.factor = 1.0
        self.reference = False

    def after_pick(self):
        self.step = self.STEP_BASE
        self.configure("Point de base (Entrée : centre de l'objet) :")

    def _size(self):
        """Distance souris qui vaut un facteur 1 : demi-diagonale de l'emprise de l'objet."""
        layer, feature = self.target
        bb = feature.geometry().boundingBox()
        return max((bb.width() ** 2 + bb.height() ** 2) ** 0.5 / 2, self.canvas().mapUnitsPerPixel() * 10)

    def _ask_factor(self):
        self.step = self.STEP_FACTOR
        self.points = [self.base]
        if self.reference:
            self.configure("Référence : longueur actuelle, puis nouvelle longueur (R : facteur) :",
                           [("ref", "Long. actuelle", "m"), ("new", "Nouvelle", "m")])
        else:
            self.configure("Facteur d'échelle (R : référence) :", [("factor", "Facteur", "")])

    def compute_step(self, mouse):
        if self.step == self.STEP_BASE:
            return mouse, None, None, {}, ""
        dist = G.distance(self.base, mouse)
        if self.reference:
            ref, new = self.dyn.locked("ref"), self.dyn.locked("new")
            new_len = new if new is not None else dist
            self.factor = (new_len / ref) if ref else 1.0
            live = {"new": new_len}
        else:
            typed = self.dyn.locked("factor")
            self.factor = typed if typed is not None else dist / self._size()
            live = {"factor": self.factor}
        info = f"Facteur {self.factor:.4f}".replace(".", ",")
        return mouse, [self.base, mouse], "line", live, info

    def keyPressEvent(self, event):
        from qgis.PyQt.QtCore import Qt
        if self.step == self.STEP_FACTOR and event.key() == Qt.Key.Key_R and self.dyn is not None:
            self.reference = not self.reference
            self._ask_factor()
            event.accept()
            self.refresh()
            return
        super().keyPressEvent(event)

    def _wants(self, event):
        from qgis.PyQt.QtCore import Qt
        return super()._wants(event) or (event.key() == Qt.Key.Key_R and self.step == self.STEP_FACTOR)

    def click_step(self, pt):
        if self.step == self.STEP_BASE:
            self.base = pt
            self._ask_factor()
        else:
            self._apply()

    def finish_step(self):
        if self.step == self.STEP_BASE:
            self.base = self.center_of(*self.target)
            self._ask_factor()
        else:
            self._apply()

    def qtransform(self, layer):
        if self.step != self.STEP_FACTOR or self.factor <= 0 or self.base is None:
            return None
        return G.scale_transform(self.layer_point(self.base, layer), self.factor)

    def transform_geometry(self, layer, feature):
        q = self.qtransform(layer)
        return G.transformed(feature.geometry(), q) if q is not None else None

    def transform_attrs(self, layer, feature):
        if _kind(layer) in LINKED_KINDS:              # longueur mesuree / rayon de l'arc
            return {"longueur": round(_number(feature["longueur"], 0.0) * self.factor, 2)}
        if not is_text_layer(layer):
            return {}
        return {"taille": max(1.0, round(_number(feature["taille"], 10.0) * self.factor, 1))}

    def _apply(self):
        geom = self.transform_geometry(*self.target)
        if geom is not None:
            self.replace_geometry(geom, self.transform_attrs(*self.target), follow=True)
        self.start()


def _reoriented_angle(geom):
    """Apres un miroir, l'arc tourne en sens horaire : on le reconstruit en sens
    anti-horaire (meme sommet, meme ouverture) pour qu'angle_parts le relise.
    Sans effet sur un arc deja anti-horaire (deplacement, rotation, echelle)."""
    parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
    if not parts or len(parts[0]) < 3:
        return geom
    arc = parts[0]
    m = arc[len(arc) // 2]
    ccw = angle_parts(geom)
    if ccw is not None:
        vertex, start, sweep, r, _ = ccw
        mid_expected = G.polar_point(vertex, r, start + sweep / 2.0)
        if G.distance(mid_expected, m) < r * 1e-3:
            return geom          # deja dans le bon sens
    rebuilt = angle_parts(QgsGeometry.fromMultiPolylineXY([list(reversed(arc))] + parts[1:]))
    if rebuilt is None:
        return geom
    vertex, start, sweep, r, reaches = rebuilt
    return build_angle(vertex, start, sweep, r, reaches)
