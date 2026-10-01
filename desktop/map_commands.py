"""Bound pending UI state while Chromium is busy or still loading."""

from collections import OrderedDict
import json


class MapCommands:
    def __init__(self):
        self.pending = OrderedDict()
        self.entity_patches = {}

    def put(self, method, args, code):
        if method == 'patchRailEntities':
            self.entity_patches.update(args[0])
            code = 'window.railscope.patchRailEntities(' + json.dumps(self.entity_patches, ensure_ascii=False) + ')'
        # Independent toggles must not replace one another.
        keyed = {"setVisibility", "setOverlay", "setBaseDetail"}
        key = (method, args[0] if method in keyed and args else None)
        self.pending[key] = code
        self.pending.move_to_end(key)

    def pop(self):
        if not self.pending:
            return None
        key, code = self.pending.popitem(last=False)
        if key[0] == 'patchRailEntities':
            self.entity_patches.clear()
        return code
