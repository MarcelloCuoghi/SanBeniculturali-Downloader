"""Lazy tree model of the portal hierarchy.

Archivio -> Fondo -> Serie -> Tipologia -> Anno -> Registro. Children are fetched in the background
the first time a node is expanded; until then an expandable node carries a single "Loading..."
placeholder child so that the view shows an expansion arrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from PySide6.QtCore import QModelIndex, QObject, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QStyle

from ..models import Archive, FacetTerm, Fondo, RegisterSummary, SearchQuery, Serie
from .workers import TaskRunner

NODE_ROLE = Qt.ItemDataRole.UserRole + 1
SEARCH_PAGE_SIZE = 1000


class NodeKind(str, Enum):
    ARCHIVE = "archive"
    FONDO = "fondo"
    SERIE = "serie"
    TIPOLOGIA = "tipologia"
    ANNO = "anno"
    REGISTER = "register"
    PLACEHOLDER = "placeholder"

    @property
    def title(self) -> str:
        return {
            NodeKind.ARCHIVE: "Archive",
            NodeKind.FONDO: "Fondo",
            NodeKind.SERIE: "Series",
            NodeKind.TIPOLOGIA: "Record type",
            NodeKind.ANNO: "Year",
            NodeKind.REGISTER: "Register",
            NodeKind.PLACEHOLDER: "",
        }[self]


class LoadState(str, Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"
    ERROR = "error"


@dataclass
class Node:
    """Payload stored on every tree item (``NODE_ROLE``)."""

    kind: NodeKind
    name: str
    payload: Any = None  # Archive | Fondo | Serie | FacetTerm | RegisterSummary
    query: SearchQuery | None = None
    count: int | None = None  # number of registers when known (facet count)
    path: tuple[str, ...] = ()  # names of the ancestors (archive first)
    archive_id: int | None = None
    state: LoadState = LoadState.UNLOADED
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def expandable(self) -> bool:
        return self.kind not in (NodeKind.REGISTER, NodeKind.PLACEHOLDER)

    @property
    def label(self) -> str:
        if self.count is not None and self.kind in (NodeKind.TIPOLOGIA, NodeKind.ANNO):
            return f"{self.name} ({self.count})"
        return self.name

    @property
    def full_path(self) -> tuple[str, ...]:
        return (*self.path, self.name)

    @property
    def register(self) -> RegisterSummary | None:
        return self.payload if isinstance(self.payload, RegisterSummary) else None


def _std_icon(pixmap: QStyle.StandardPixmap) -> QIcon:
    return QApplication.style().standardIcon(pixmap)


def node_icon(kind: NodeKind) -> QIcon:
    sp = QStyle.StandardPixmap
    return _std_icon(
        {
            NodeKind.ARCHIVE: sp.SP_DriveNetIcon,
            NodeKind.FONDO: sp.SP_DirIcon,
            NodeKind.SERIE: sp.SP_DirOpenIcon,
            NodeKind.TIPOLOGIA: sp.SP_FileDialogListView,
            NodeKind.ANNO: sp.SP_FileDialogContentsView,
            NodeKind.REGISTER: sp.SP_FileIcon,
            NodeKind.PLACEHOLDER: sp.SP_BrowserReload,
        }[kind]
    )


# --------------------------------------------------------------------------- loaders (worker thread)
def _register_label(reg: RegisterSummary) -> str:
    label = reg.title or reg.id
    if reg.segnatura:
        label += f"  [{reg.segnatura}]"
    return label


def _register_nodes(portal, query: SearchQuery, base: Node) -> list[Node]:
    path = base.full_path
    return [
        Node(
            NodeKind.REGISTER,
            _register_label(r),
            r,
            path=path,
            archive_id=base.archive_id,
            state=LoadState.LOADED,
        )
        for r in portal.iter_registers(query, size=SEARCH_PAGE_SIZE)
    ]


def _facet_nodes(portal, node: Node, field_name: str, kind: NodeKind) -> list[Node]:
    """Children of a query node: the terms of ``field_name`` or, if absent, the registers."""
    assert node.query is not None
    page = portal.search(node.query, page=1, size=2)
    facet = page.facets.get(field_name)
    terms = [t for t in facet.terms if t.query not in node.query.facets] if facet else []
    if not terms:
        return _register_nodes(portal, node.query, node)
    path = node.full_path
    return [
        Node(
            kind, t.value, t, query=node.query.refine(t), count=t.count, path=path, archive_id=node.archive_id
        )
        for t in terms
    ]


def load_children(portal, node: Node) -> list[Node]:
    """Fetch the children of ``node`` (blocking; runs in a worker thread)."""
    if node.kind is NodeKind.ARCHIVE:
        archive: Archive = node.payload
        return [
            Node(NodeKind.FONDO, f.name, f, path=node.full_path, archive_id=archive.id)
            for f in portal.fondi(archive.id)
        ]
    if node.kind is NodeKind.FONDO:
        fondo: Fondo = node.payload
        return [
            Node(
                NodeKind.SERIE,
                s.name,
                s,
                query=SearchQuery(serie=s.id),
                path=node.full_path,
                archive_id=fondo.archive_id,
            )
            for s in fondo.series
        ]
    if node.kind is NodeKind.SERIE:
        return _facet_nodes(portal, node, "tipologia_ss", NodeKind.TIPOLOGIA)
    if node.kind is NodeKind.TIPOLOGIA:
        return _facet_nodes(portal, node, "anni_is", NodeKind.ANNO)
    if node.kind is NodeKind.ANNO:
        assert node.query is not None
        return _register_nodes(portal, node.query, node)
    return []


def resolve_fondo_query(portal, fondo: Fondo) -> tuple[SearchQuery | None, int | None]:
    """Search query selecting every register of a fondo (via the ``primo_livello_label_s`` facet)."""
    base = SearchQuery(archivio=fondo.archive_id)
    page = portal.search(base, page=1, size=2)
    facet = page.facets.get("primo_livello_label_s")
    if facet:
        wanted = fondo.name.strip().casefold()
        for term in facet.terms:
            if term.value.strip().casefold() == wanted:
                return base.refine(term), term.count
    return None, None


def archive_node(archive: Archive) -> Node:
    return Node(
        NodeKind.ARCHIVE,
        archive.name,
        archive,
        query=SearchQuery(archivio=archive.id),
        archive_id=archive.id,
    )


def serie_node(serie: Serie, path: tuple[str, ...], archive_id: int | None) -> Node:
    return Node(
        NodeKind.SERIE, serie.name, serie, SearchQuery(serie=serie.id), path=path, archive_id=archive_id
    )


def term_of(node: Node) -> FacetTerm | None:
    return node.payload if isinstance(node.payload, FacetTerm) else None


# --------------------------------------------------------------------------- model
class NavigatorModel(QStandardItemModel):
    """Tree of :class:`Node` items with lazy, background loading of children."""

    #: (message) a load started / finished, for the status bar
    status = Signal(str)
    #: (node, error message)
    loadFailed = Signal(object, str)
    #: (QStandardItem parent or None) children have been added
    childrenLoaded = Signal(object)

    def __init__(self, portal, runner: TaskRunner, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.portal = portal
        self.runner = runner
        self.setHorizontalHeaderLabels(["Portale Antenati"])
        self._archives_state = LoadState.UNLOADED

    # ------------------------------------------------------------------ items
    def make_item(self, node: Node) -> QStandardItem:
        item = QStandardItem(node_icon(node.kind), node.label)
        item.setEditable(False)
        item.setData(node, NODE_ROLE)
        tip = " › ".join(node.full_path)
        reg = node.register
        if reg is not None:
            tip = f"{tip}\n{reg.id}" + (f" - {reg.tipologia}" if reg.tipologia else "")
        item.setToolTip(tip)
        if node.expandable and node.state is not LoadState.LOADED:
            item.appendRow(self._placeholder("Loading…" if node.state is LoadState.LOADING else "…"))
        return item

    def _placeholder(self, text: str) -> QStandardItem:
        ph = QStandardItem(node_icon(NodeKind.PLACEHOLDER), text)
        ph.setEditable(False)
        ph.setSelectable(False)
        ph.setData(Node(NodeKind.PLACEHOLDER, text, state=LoadState.LOADED), NODE_ROLE)
        ph.setForeground(QApplication.palette().placeholderText())
        return ph

    @staticmethod
    def node_of(item: QStandardItem | None) -> Node | None:
        if item is None:
            return None
        node = item.data(NODE_ROLE)
        return node if isinstance(node, Node) else None

    def node_at(self, index: QModelIndex) -> Node | None:
        return self.node_of(self.itemFromIndex(index)) if index.isValid() else None

    # ------------------------------------------------------------------ loading
    def load_archives(self, refresh: bool = False) -> None:
        if self._archives_state is LoadState.LOADING:
            return
        self._archives_state = LoadState.LOADING
        self.status.emit("Loading archives…")
        self.runner.submit(
            self.portal.archives,
            refresh,
            on_done=self._archives_loaded,
            on_error=self._archives_failed,
        )

    def _archives_loaded(self, archives: list[Archive]) -> None:
        self._archives_state = LoadState.LOADED
        self.removeRows(0, self.rowCount())
        for archive in archives:
            self.appendRow(self.make_item(archive_node(archive)))
        self.status.emit(f"{len(archives)} archives")
        self.childrenLoaded.emit(None)

    def _archives_failed(self, exc: BaseException) -> None:
        self._archives_state = LoadState.ERROR
        self.loadFailed.emit(None, f"Could not load the archives: {exc}")

    def fetch(self, item: QStandardItem) -> bool:
        """Start loading the children of ``item`` if needed. Returns True if a load started."""
        node = self.node_of(item)
        if node is None or not node.expandable or node.state in (LoadState.LOADED, LoadState.LOADING):
            return False
        node.state = LoadState.LOADING
        if item.rowCount() == 0:
            item.appendRow(self._placeholder("Loading…"))
        else:
            item.child(0).setText("Loading…")
        self.status.emit(f"Loading {node.name}…")
        self.runner.submit(
            load_children,
            self.portal,
            node,
            on_done=lambda children, it=item, n=node: self._children_loaded(it, n, children),
            on_error=lambda exc, it=item, n=node: self._children_failed(it, n, exc),
        )
        return True

    def _item_alive(self, item: QStandardItem, node: Node) -> bool:
        try:
            return self.node_of(item) is node and item.model() is self
        except RuntimeError:  # underlying C++ item deleted (e.g. archives reloaded)
            return False

    def _children_loaded(self, item: QStandardItem, node: Node, children: list[Node]) -> None:
        node.state = LoadState.LOADED
        if not self._item_alive(item, node):
            return
        item.removeRows(0, item.rowCount())
        if children:
            item.appendRows([self.make_item(c) for c in children])
        else:
            empty = self._placeholder("(empty)")
            item.appendRow(empty)
        self.status.emit(f"{node.name}: {len(children)} items")
        self.childrenLoaded.emit(item)

    def _children_failed(self, item: QStandardItem, node: Node, exc: BaseException) -> None:
        node.state = LoadState.ERROR
        if self._item_alive(item, node) and item.rowCount():
            item.child(0).setText("Could not load - collapse and expand to retry")
        self.loadFailed.emit(node, f"Could not load {node.name}: {exc}")


class NavigatorFilter(QSortFilterProxyModel):
    """Case-insensitive recursive filter; children of a matching node stay visible."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self.setAutoAcceptChildRows(True)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(0)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self.filterRegularExpression().pattern():
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        node = index.data(NODE_ROLE)
        if isinstance(node, Node) and node.kind is NodeKind.PLACEHOLDER:
            return False
        return super().filterAcceptsRow(source_row, source_parent)
