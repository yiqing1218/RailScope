"""Authoritative catalog edits and command history, independent of Qt views.

Exchange files are seeds, not a second writable project. A local value wins
over its seed on every load. Persistence succeeds before state/history changes.
"""
from copy import deepcopy
import json
from pathlib import Path
try:
    from .persistence import write_json_atomic
except ImportError:
    from persistence import write_json_atomic


def read_overrides(path):
    path = Path(path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(payload, dict) and payload.get('schema') == 'railscope.catalog-exchange.v1':
        payload = payload.get('overrides')
    return validate_overrides(payload, path)


def validate_overrides(payload, path):
    if not isinstance(payload, dict):
        raise ValueError(f'铁路目录文件格式无效：{path}')
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValueError(f'铁路目录对象格式无效：{path}')
        folder = value.get('folder_path')
        if (type(value.get('archived', False)) is not bool or
            folder is not None and (not isinstance(folder, list) or not folder or
                any(not isinstance(part, str) or not part.strip() for part in folder))):
            raise ValueError(f'铁路目录属性格式无效：{path} / {key}')
    return payload


class CatalogWorkspace:
    def __init__(self, path):
        self.path = Path(path)
        self.values = {}
        self.undo_stack = []
        self.redo_stack = []
        self.load_failed = False

    def load(self, seeds=()):
        proposed = {}
        self.load_failed = True
        for seed in (*seeds, self.path):
            for key, value in read_overrides(seed).items():
                proposed[key] = {**proposed.get(key, {}), **value}
        self.values.clear()
        self.values.update(proposed)
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.load_failed = False

    def _commit(self, proposed):
        if self.load_failed:
            raise ValueError('工作区未成功载入；请先修复文件并重新载入，原文件保留')
        validate_overrides(proposed, self.path)
        write_json_atomic(self.path, proposed)
        self.values.clear()
        self.values.update(proposed)

    def update(self, changes):
        if not changes:
            return
        before = {key: deepcopy(self.values.get(key)) for key in changes}
        proposed = dict(self.values)
        for key, change in changes.items():
            if not isinstance(key, str) or not isinstance(change, dict):
                raise ValueError('目录批量修改内容无效')
            proposed[key] = {**proposed.get(key, {}), **deepcopy(change)}
        if proposed == self.values:
            return
        self._commit(proposed)
        self.undo_stack.append(before)
        del self.undo_stack[:-30]
        self.redo_stack.clear()

    def _restore(self, source, target):
        if not source:
            return None
        old = deepcopy(self.values)
        command = source[-1]
        reverse = {key: deepcopy(old.get(key)) for key in command}
        proposed = dict(self.values)
        for key, value in command.items():
            if value is None:
                proposed.pop(key, None)
                # Older corridors may still reference an undone line assembly.
                if key.startswith('line-assembly:') and key in old:
                    proposed[key] = {**old[key], 'active': False}
            else:
                proposed[key] = deepcopy(value)
        changed = {key for key in command if old.get(key) != proposed.get(key)}
        self._commit(proposed)
        source.pop()
        target.append(reverse)
        return old, changed

    def undo(self):
        return self._restore(self.undo_stack, self.redo_stack)

    def redo(self):
        return self._restore(self.redo_stack, self.undo_stack)
