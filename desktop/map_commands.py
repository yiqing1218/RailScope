"""Bound pending UI state while Chromium is busy or still loading."""

from collections import OrderedDict


class MapCommands:
    def __init__(self):
        self.pending = OrderedDict()

    def put(self, method, args, code):
        # Independent toggles must not replace one another.
        keyed = {"setVisibility", "setOverlay", "setBaseDetail"}
        key = (method, args[0] if method in keyed and args else None)
        self.pending[key] = code
        self.pending.move_to_end(key)

    def pop(self):
        return self.pending.popitem(last=False)[1] if self.pending else None
