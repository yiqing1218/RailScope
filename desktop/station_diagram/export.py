"""Export the same independent SVG drawing to vector PDF or PNG."""

from pathlib import Path
from .helpers import ensure_export_font


def write_diagram(path, svg, options):
    """Exact PNG pixel size and physical DPI; vector SVG/PDF retain paths."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in (".svg", ".png", ".pdf"):
        raise ValueError("仅支持 SVG / PNG / PDF")
    if suffix == ".svg":
        path.write_text(svg, encoding="utf-8")
        return
    from PySide6.QtCore import QRectF, QSizeF, QMarginsF
    from PySide6.QtGui import QImage, QPainter, QPdfWriter, QPageSize, QPageLayout
    from PySide6.QtSvg import QSvgRenderer

    ensure_export_font()
    renderer = QSvgRenderer(svg.encode("utf-8"))
    if not renderer.isValid():
        raise ValueError("示意图 SVG 无法渲染")
    size = renderer.defaultSize()
    if suffix == ".pdf":
        device = QPdfWriter(str(path))
        device.setResolution(options.dpi)
        device.setPageSize(
            QPageSize(
                QSizeF(
                    size.width() * 25.4 / options.dpi,
                    size.height() * 25.4 / options.dpi,
                ),
                QPageSize.Unit.Millimeter,
            )
        )
        device.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    else:
        device = QImage(size, QImage.Format.Format_ARGB32)
        device.fill("white")
        ppm = round(options.dpi / 0.0254)
        device.setDotsPerMeterX(ppm)
        device.setDotsPerMeterY(ppm)
    painter = QPainter(device)
    if not painter.isActive():
        raise ValueError("无法打开导出绘图设备")
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, device.width(), device.height()))
    finally:
        painter.end()
    if suffix == ".png" and not device.save(str(path)):
        raise ValueError("示意图无法写入")
