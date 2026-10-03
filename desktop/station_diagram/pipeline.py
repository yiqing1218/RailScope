"""Shape-led pipeline: per-yard bending, shared junctions and linear outlets."""
def build_layout(repo, context=(), options=None):
    if options is not None and options.layout_mode == 'source_shape':
        from .shape_layout import build_layout as build
    else:
        from .yard_layout import build_layout as build
    return build(repo, context, options)

__all__ = ["build_layout"]
