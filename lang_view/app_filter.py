class AppFilter:
    """Decide whether to OCR a frame given the frontmost application.

    Allowlist takes precedence: if any allowlist entries are set, only
    those apps pass. Otherwise an empty allowlist plus a blocklist
    means "everything except the blocklisted apps".

    Matching is case-insensitive and substring-based, so an entry
    "Chrome" matches "Google Chrome".
    """

    def __init__(self, allow=None, block=None):
        self.allow = [a.lower() for a in (allow or []) if a]
        self.block = [b.lower() for b in (block or []) if b]

    def allows(self, app_name):
        if app_name is None:
            return not self.allow
        name = app_name.lower()
        if self.allow:
            return any(a in name for a in self.allow)
        return not any(b in name for b in self.block)

    @classmethod
    def parse(cls, allow_str=None, block_str=None):
        allow = [s.strip() for s in (allow_str or "").split(",") if s.strip()]
        block = [s.strip() for s in (block_str or "").split(",") if s.strip()]
        return cls(allow=allow, block=block)
