# -*- coding: utf-8 -*-
"""I2：可撤销注册的最小实现。

- register 返回 handle
- dispose 幂等
- 同 key 后注册遮蔽先注册；dispose 后自动回退
- scope 内注册只在 scope 可见，global 兜底
"""


class Registration:
    def __init__(self, registry, key, scope, value):
        self.registry = registry
        self.key = key
        self.scope = scope
        self.value = value
        self.disposed = False

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.registry._remove(self)


class Registry:
    def __init__(self):
        self._stacks = {}

    def register(self, key, value, scope="global"):
        key = str(key)
        scope = str(scope)
        h = Registration(self, key, scope, value)
        self._stacks.setdefault((scope, key), []).append(h)
        return h

    def get(self, key, scope="global"):
        key = str(key)
        scope = str(scope)
        stack = self._stacks.get((scope, key)) or []
        if stack:
            return stack[-1].value
        if scope != "global":
            return self.get(key, "global")
        return None

    def dispose(self, key, scope="global"):
        stack = self._stacks.get((str(scope), str(key))) or []
        if stack:
            stack[-1].dispose()

    def _remove(self, h):
        stack = self._stacks.get((h.scope, h.key)) or []
        try:
            stack.remove(h)
        except ValueError:
            return
        if not stack:
            self._stacks.pop((h.scope, h.key), None)
