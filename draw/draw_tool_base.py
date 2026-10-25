"""Base commune des outils de dessin : accrochage, ortho, apercu,
saisie dynamique au clavier et enregistrement dans la couche cible."""
from qgis.core import QgsApplication, QgsCsException, QgsPointLocator, QgsProject
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator, QgsVertexMarker
from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QColor

from . import geometry as G
from . import target as T
from .crs import Frame, work_crs
from .dynamic_input import DynamicInput
from .osnap import LABELS as OSNAP_LABELS, ObjectSnapper

ACCENT = QColor("#ea580c")
OSNAP_COLOR = QColor("#16a34a")


def _icon(name):
    """Type de marqueur QgsVertexMarker (repli sur la boite si absent de cette version)."""
    types = getattr(QgsVertexMarker, "IconType", QgsVertexMarker)
    return getattr(types, name, getattr(types, "ICON_BOX"))


OSNAP_ICONS = {
    "endpoint": "ICON_BOX", "midpoint": "ICON_TRIANGLE", "center": "ICON_CIRCLE",
    "quadrant": "ICON_RHOMBUS", "nearest": "ICON_DOUBLE_TRIANGLE", "opposite": "ICON_CROSS",
    "intersection": "ICON_X", "perpendicular": "ICON_INVERTED_TRIANGLE",
}
TYPED_CHARS = "0123456789.,-"
HANDLED_KEYS = {
    Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Backspace, Qt.Key.Key_Return,
    Qt.Key.Key_Enter, Qt.Key.Key_Escape, Qt.Key.Key_F8,
    Qt.Key.Key_U, Qt.Key.Key_C, Qt.Key.Key_A, Qt.Key.Key_F3,
}


class DrawTool(QgsMapTool):
    """Les sous-classes definissent TOOL_NAME, start(), compute(mouse), on_click(pt)
    et, si besoin, on_finish(), on_close(), undo_point()."""

    TOOL_NAME = ""

    def __init__(self, canvas, plugin, action):
        super().__init__(canvas)
        self.plugin = plugin
        self.action = action
        self.points = []        # points valides, SCR de travail (self.frame.crs)
        self.mouse = None       # position souris (accrochee si accrochage)
        self.current = None     # point contraint (ortho, valeurs tapees)
        self.snapped = False
        self.dyn = None
        self.snap_indicator = None
        self.rb_line = self.rb_poly = self.guide = None
        self.osnap = None
        self.osnap_marker = None
        self.snap_mode = None   # mode d'accrochage Box_cad du point courant
        self._commits = []      # [(layer_id, fid ou None si couche active)]
        # angle tape relatif au segment precedent (deviation), sauf si bascule par A
        self.angle_absolute = False
        # SCR de travail : metres vrais meme si la carte est en degres ou en Web Mercator
        self.frame = None
        self.setCursor(QgsApplication.getThemeCursor(QgsApplication.Cursor.CapturePoint))

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def activate(self):
        super().activate()
        canvas = self.canvas()
        self.frame = Frame(canvas)
        self.dyn = DynamicInput(canvas, self.plugin.show_draw_status)
        self.snap_indicator = QgsSnapIndicator(canvas)
        self.rb_line = self._rubber(T.LINE)
        self.rb_poly = self._rubber(T.POLYGON)
        self.guide = QgsRubberBand(canvas, T.LINE)
        self.guide.setStrokeColor(QColor(120, 120, 120, 200))
        self.guide.setLineStyle(Qt.PenStyle.DashLine)
        self.guide.setWidth(1)
        self.osnap = ObjectSnapper(canvas, self.plugin)
        self.osnap_marker = QgsVertexMarker(canvas)
        self.osnap_marker.setColor(OSNAP_COLOR)
        self.osnap_marker.setPenWidth(2)
        self.osnap_marker.setIconSize(14)
        self.osnap_marker.hide()
        canvas.installEventFilter(self)
        canvas.destinationCrsChanged.connect(self._on_crs_changed)
        canvas.extentsChanged.connect(self._on_extent_changed)
        self.start()
        self._notice_work_crs()        # apres start() : le bandeau d'Aligner ne le cache pas

    def deactivate(self):
        canvas = self.canvas()
        canvas.removeEventFilter(self)
        for signal, slot in ((canvas.destinationCrsChanged, self._on_crs_changed),
                             (canvas.extentsChanged, self._on_extent_changed)):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass
        for rb in (self.rb_line, self.rb_poly, self.guide, self.osnap_marker):
            if rb is not None:
                canvas.scene().removeItem(rb)
        self.rb_line = self.rb_poly = self.guide = self.osnap_marker = None
        self.osnap = None
        if self.snap_indicator is not None:
            self.snap_indicator.setMatch(QgsPointLocator.Match())
            self.snap_indicator = None
        if self.dyn is not None:
            self.dyn.hide()
            self.dyn.deleteLater()
            self.dyn = None
        self.plugin.show_draw_status("")
        self.points = []
        super().deactivate()
        self.plugin._on_tool_deactivated(self.action)

    def _on_crs_changed(self):
        """SCR de la carte change pendant l'outil : nouveau SCR de travail, on recommence."""
        self.frame = Frame(self.canvas())
        self.mouse = self.current = None
        self.start()
        self._notice_work_crs()

    def _idle(self):
        """Aucune forme ni transformation en cours."""
        return not self.points and getattr(self, "target", None) is None and getattr(self, "step", 0) == 0

    def _on_extent_changed(self):
        """Vue deplacee ou zoomee : le SCR de travail se choisit au centre de la vue (Web
        Mercator vu du monde entier puis zoome sur la France, par exemple). Jamais au
        milieu d'une forme : il ne change qu'entre deux formes."""
        if self.frame is None or not self._idle():
            return
        crs = work_crs(self.canvas())
        if crs != self.frame.crs:
            self.frame = Frame(self.canvas(), crs)
            self.mouse = self.current = None       # position lue dans l'ancien SCR
            self._notice_work_crs()

    def _notice_work_crs(self):
        """Signale (une fois par session et par couple de SCR) que les mesures se font dans
        un autre SCR que celui de la carte ; avertit si aucun SCR metrique n'a ete trouve."""
        frame = self.frame
        if frame.crs.isGeographic():
            self.plugin.warn("La carte est en coordonnées géographiques et aucun SCR métrique n'a été "
                             "trouvé : les longueurs seraient en degrés.")
            return
        if frame.same:
            return
        key = (frame.map_crs.authid(), frame.crs.authid())
        if key in self.plugin.work_notices:
            return
        self.plugin.work_notices.add(key)
        self.plugin.iface.messageBar().pushInfo(
            "Box_cad", f"Mesures en mètres vrais : calcul en {frame.describe()}, "
                       f"carte en {frame.map_crs.authid() or 'SCR sans code'}.")

    def _rubber(self, geom_type):
        rb = QgsRubberBand(self.canvas(), geom_type)
        rb.setStrokeColor(ACCENT)
        rb.setFillColor(QColor(234, 88, 12, 45))
        rb.setWidth(2)
        return rb

    # ------------------------------------------------------------------
    # Souris
    # ------------------------------------------------------------------

    def wants_osnap(self):
        """Accrochage objet souhaite a l'etape courante (pas pour designer un objet)."""
        return True

    def _read_mouse(self, event):
        # 1) accrochage objet Box_cad (centre, quadrant, oppose...), 2) accrochage QGIS ;
        # le point retenu est exprime dans le SCR de travail
        self.snap_mode = None
        frame = self.frame
        if self.osnap is not None and self.plugin.osnap_on and self.wants_osnap():
            hit = self.osnap.find(frame.from_map(event.mapPoint()), self.points[-1] if self.points else None,
                                  crs=frame.crs)
            if hit is not None:
                self.mouse, self.snap_mode = hit
                self.snapped = True
                self.snap_indicator.setMatch(QgsPointLocator.Match())
                self.osnap_marker.setIconType(_icon(OSNAP_ICONS[self.snap_mode]))
                self.osnap_marker.setCenter(frame.to_map(self.mouse))
                self.osnap_marker.show()
                return
        if self.osnap_marker is not None:
            self.osnap_marker.hide()
        match = self.canvas().snappingUtils().snapToMap(event.pos())
        self.snap_indicator.setMatch(match)
        self.snapped = match.isValid()
        self.mouse = frame.from_map(match.point() if self.snapped else event.mapPoint())

    def canvasMoveEvent(self, event):
        if self.dyn is None:
            return
        try:
            self._read_mouse(event)
        except QgsCsException:
            return              # curseur hors du domaine du SCR de travail
        self.refresh()
        self.dyn.follow(event.pos())

    def canvasReleaseEvent(self, event):
        if self.dyn is None:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.on_finish()
            self.refresh()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        try:
            self._read_mouse(event)
        except QgsCsException:
            return
        self.refresh()
        if self.current is not None:
            self.on_click(self.current)
            self.refresh()

    # ------------------------------------------------------------------
    # Clavier
    # ------------------------------------------------------------------

    def _wants(self, event):
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            return False
        return event.key() in HANDLED_KEYS or (event.text() != "" and event.text() in TYPED_CHARS)

    def eventFilter(self, obj, event):
        # Tab sert normalement a changer de widget, et certaines touches sont des
        # raccourcis QGIS : on les reserve tant que l'outil de dessin est actif.
        if event.type() == QEvent.Type.ShortcutOverride and self._wants(event):
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(event)
            return True
        return False

    def keyPressEvent(self, event):
        if self.dyn is None or not self._wants(event):
            event.ignore()
            return
        key, text = event.key(), event.text()
        if text and text in TYPED_CHARS:
            self.dyn.type_char(text)
        elif key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.dyn.next_field()
        elif key == Qt.Key.Key_Backspace:
            if not self.dyn.backspace():
                self.undo_point()
        elif key == Qt.Key.Key_U:
            self.undo_point()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            had_input = self.dyn.has_input()
            self.dyn.commit_typing()
            self.refresh()
            if had_input and self.current is not None:
                self.on_click(self.current)
            else:
                self.on_finish()
        elif key == Qt.Key.Key_Escape:
            if self.dyn.has_input():
                self.dyn.clear_input()
            else:
                self.on_finish(cancel=True)
        elif key == Qt.Key.Key_F8:
            self.plugin.action_ortho.toggle()
        elif key == Qt.Key.Key_F3:
            self.plugin.action_osnap.toggle()
            if not self.plugin.osnap_on and self.osnap_marker is not None:
                self.osnap_marker.hide()
                self.snap_mode = None
        elif key == Qt.Key.Key_A:
            self.angle_absolute = not self.angle_absolute
            self.on_angle_mode()
        elif key == Qt.Key.Key_C:
            self.on_close()
        event.accept()
        self.refresh()

    # ------------------------------------------------------------------
    # Apercu
    # ------------------------------------------------------------------

    def refresh(self):
        """Recalcule le point contraint et l'apercu depuis la derniere position souris."""
        if self.dyn is None or self.mouse is None:
            return
        self.guide.reset(T.LINE)
        current, preview, kind, live, info = self.compute(self.mouse)
        self.current = current
        self.rb_line.reset(T.LINE)
        self.rb_poly.reset(T.POLYGON)
        # apercu des polygones : rempli seulement si le bouton Remplissage est actif
        self.rb_poly.setFillColor(QColor(234, 88, 12, 60) if self.plugin.draw_fill else QColor(0, 0, 0, 0))
        if preview and len(preview) >= 2:
            if kind == "polygon" and len(preview) >= 3:
                self.rb_poly.setToGeometry(self.frame.geom_to_map(G.polygon_geometry(preview)), None)
            else:
                self.rb_line.setToGeometry(self.frame.geom_to_map(G.line_geometry(preview)), None)
        self.dyn.set_live(live or {})
        if self.snap_mode:
            label = OSNAP_LABELS[self.snap_mode]
            info = f"{label} · {info}" if info else label
        self.dyn.set_info(info)
        self.draw_extra()

    def draw_extra(self):
        """Apercu supplementaire propre a un outil (ex. objet tourne)."""

    def relative_ref(self, ref_angle):
        """Direction de reference effective : None en mode angle absolu."""
        return None if self.angle_absolute else ref_angle

    def angle_field(self, has_ref):
        """Champ angle : relatif (deviation) des qu'il y a un segment precedent."""
        if not has_ref:
            return ("angle", "Angle", "°")
        return ("angle", "Angle abs." if self.angle_absolute else "Angle rel.", "°")

    def shown_angle(self, ang, ref_angle):
        """Valeur affichee dans le champ angle : deviation en mode relatif."""
        ref = self.relative_ref(ref_angle)
        return ang if ref is None else G.deflection(ang, ref)

    def on_angle_mode(self):
        """Touche A : les outils a segments mettent a jour le libelle du champ."""

    def constrain_from(self, base, mouse, length_key="length", angle_key="angle", ref_angle=None):
        """Point depuis `base` : valeurs verrouillees > accrochage > ortho.
        Avec `ref_angle` (direction du segment precedent) et hors mode absolu, l'angle
        tape est une deviation (0 = tout droit) et l'ortho s'aimante par
        rapport a ce segment. Retourne (point, angle absolu, longueur)."""
        ref = self.relative_ref(ref_angle)
        origin = ref if ref is not None else 0.0     # repere des angles tapes / aimantes
        locked_len = self.dyn.locked(length_key)
        locked_ang = self.dyn.locked(angle_key)
        dist = G.distance(base, mouse)
        ang = G.angle_of(base, mouse) if dist > 0 else origin
        if self.snapped and locked_len is None and locked_ang is None:
            return mouse, ang, dist        # l'accrochage objet l'emporte (comme en DAO)
        guide = False
        if locked_ang is not None:
            ang, guide = (origin + locked_ang) % 360.0, True
        elif self.plugin.draw_ortho:
            ang, guide = (origin + G.ortho_angle((ang - origin) % 360.0)) % 360.0, True
        if locked_len is not None:
            dist = abs(locked_len)
        if guide:
            far = self.frame.pixel() * max(self.canvas().width(), self.canvas().height())
            self.guide.setToGeometry(self.frame.geom_to_map(G.line_geometry([base, G.polar_point(base, far, ang)])),
                                     None)
        return G.polar_point(base, dist, ang), ang, dist

    def configure(self, prompt, fields=()):
        self.dyn.configure(prompt, list(fields))

    # ------------------------------------------------------------------
    # Enregistrement
    # ------------------------------------------------------------------

    def commit(self, geom, kind, extra=None):
        """Ajoute la geometrie (SCR de travail) a la couche active compatible,
        sinon a la couche brouillon. Retourne True si l'entite est creee.
        `extra` : attributs du brouillon (ex. longueur mesuree d'une cote)."""
        iface = self.plugin.iface
        work_crs = self.frame.crs
        layer = T.active_target(iface, kind)
        if layer is not None:
            if not T.add_to_active(iface, layer, T.fit_geometry(geom, layer, work_crs)):
                return False
            self._commits.append((layer.id(), None))
            return True
        layer, created = T.draft_layer(kind, work_crs)
        if created:
            iface.messageBar().pushInfo(
                "Box_cad", f"Couche « {layer.name()} » créée en mémoire : exportez-la pour la conserver "
                           "(sinon elle disparaît à la fermeture de QGIS).")
        extra = dict(extra or {})
        # mesures dans le SCR de travail (metres vrais), pas dans celui du brouillon
        extra.setdefault("longueur", round(geom.length(), 2))
        extra.setdefault("surface", round(geom.area(), 2))
        for name, value in (getattr(self.plugin, "current_props", None) or {}).items():
            if value is not None:
                extra.setdefault(name, value)     # proprietes courantes (champs absents ignores)
        if kind == "polygon":
            extra.setdefault("rempli", 1 if self.plugin.draw_fill else 0)
        fid = T.add_to_draft(layer, T.fit_geometry(geom, layer, work_crs), self.TOOL_NAME, extra)
        if fid is None:
            self.plugin.warn(f"Ajout impossible dans « {layer.name()} ».")
            return False
        self._commits.append((layer.id(), fid))
        self.plugin._set_undo(('add_features', layer.id(), [fid]))
        return True

    def undo_last_commit(self):
        """Retire la derniere entite creee par cet outil (U dans l'outil Ligne)."""
        if not self._commits:
            return
        layer_id, fid = self._commits.pop()
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return
        if fid is None:
            layer.undoStack().undo()
        else:
            layer.dataProvider().deleteFeatures([fid])
            layer.triggerRepaint()
            self.plugin._discard_undo(('add_features', layer_id, [fid]))
        layer.triggerRepaint()

    # ------------------------------------------------------------------
    # Comportements par defaut
    # ------------------------------------------------------------------

    def start(self):
        self.points = []

    def on_finish(self, cancel=False):
        self.start()

    def on_close(self):
        pass

    def undo_point(self):
        if self.points:
            self.points.pop()
        if not self.points:
            self.start()
